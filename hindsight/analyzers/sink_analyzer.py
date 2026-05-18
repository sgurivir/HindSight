#!/usr/bin/env python3
"""
Sink Analyzer — async/await parallel LLM analysis to determine
whether functions are security-relevant data sinks.

A "sink" is any operation where attacker-controlled data has security
impact — code execution, state mutation, privilege change, resource
allocation, etc. This analyzer is OS and programming language agnostic.

Mirrors the architecture of ExternalInputAnalyzer: batched LLM calls,
rate limiting, pre-fetched function bodies, UUID correlation.

Output: data_sinks.json — flat list of functions classified as sinks,
with location and reason.
"""

import asyncio
import json
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.constants import (
    SINK_ANALYSIS_RATE_LIMIT,
    SINK_ANALYSIS_DEFAULT_WORKERS,
    SINK_ANALYSIS_MAX_TOOL_ITERATIONS,
    SINK_ANALYSIS_BATCH_SIZE,
    SINK_ANALYSIS_TOKEN_BUDGET_RATIO,
    SINK_ANALYSIS_CHARS_PER_TOKEN,
    LLM_PROVIDER_RATE_WINDOW_SECONDS,
)
from ..core.async_infra import RateLimiter
from ..core.mcp_tools.code_navigation_server import CodeNavigationServer
from ..utils.log_util import get_logger

logger = get_logger(__name__)


class SinkAnalyzer:
    """
    Async analyzer that uses LLM to determine whether each function
    in a call tree is a security-relevant data sink.

    Operates in batched mode: sends up to SINK_ANALYSIS_BATCH_SIZE functions
    per LLM request, with pre-fetched function bodies included inline.
    Each function is tagged with a UUID for reliable response correlation.
    """

    def __init__(
        self,
        mcp_server: CodeNavigationServer,
        llm_request_fn: Callable,
        rate_limit: int = SINK_ANALYSIS_RATE_LIMIT,
        max_workers: int = SINK_ANALYSIS_DEFAULT_WORKERS,
        max_tool_iterations: int = SINK_ANALYSIS_MAX_TOOL_ITERATIONS,
        batch_size: int = SINK_ANALYSIS_BATCH_SIZE,
        context_window: int = 200_000,
        on_result_callback: Optional[Callable[[str, bool, str, str], None]] = None,
    ):
        """
        Args:
            mcp_server: CodeNavigationServer instance for tool execution
            llm_request_fn: Async callable(system_prompt, messages) -> str
            rate_limit: Maximum LLM requests per minute
            max_workers: Number of parallel workers
            max_tool_iterations: Max tool-call rounds per function (single-function fallback)
            batch_size: Max functions per batched LLM call
            context_window: Model context window in tokens
            on_result_callback: Optional callback(func_name, is_sink, reason, category)
        """
        self.mcp_server = mcp_server
        self.llm_request_fn = llm_request_fn
        self.rate_limiter = RateLimiter(max_requests=rate_limit,
                                        window_seconds=LLM_PROVIDER_RATE_WINDOW_SECONDS)
        self.max_workers = max_workers
        self.max_tool_iterations = max_tool_iterations
        self.batch_size = batch_size
        self.context_window = context_window
        self._on_result_callback = on_result_callback
        # func_name -> (is_sink, reason, category)
        self._results: Dict[str, Tuple[bool, str, str]] = {}

    # ─── Token budget estimation ─────────────────────────────────────────

    def _estimate_tokens(self, text: str) -> int:
        return len(text) // SINK_ANALYSIS_CHARS_PER_TOKEN

    def _get_input_token_budget(self) -> int:
        return int(self.context_window * SINK_ANALYSIS_TOKEN_BUDGET_RATIO)

    # ─── Batch prompt building ───────────────────────────────────────────

    def _build_batch_system_prompt(self) -> str:
        return _BATCH_SYSTEM_PROMPT

    def _build_batch_user_prompt(self, batch: List[Dict[str, str]]) -> str:
        parts = [
            "Analyze the following functions and determine whether each is a security-relevant data sink.\n"
            "For each function, examine its source code, what operations it performs, and whether attacker-controlled data reaching it could have security impact.\n\n"
        ]

        for item in batch:
            parts.append(f"---\n## Function ID: `{item['id']}`\n")
            parts.append(f"**Name:** `{item['function_name']}`\n\n")
            parts.append(f"**Source code:**\n```\n{item['body']}\n```\n\n")

        parts.append(
            "---\n\nNow analyze ALL functions above and respond with the JSON array "
            "containing one result per function (matched by `id`)."
        )
        return "".join(parts)

    # ─── Batch response parsing ──────────────────────────────────────────

    def _parse_batch_response(self, text: str, batch: List[Dict[str, str]]) -> Dict[str, Tuple[bool, str, str]]:
        """Parse the LLM batch response and correlate results by UUID.

        Returns dict mapping function_name -> (is_sink, reason, category).
        """
        results: Dict[str, Tuple[bool, str, str]] = {}

        id_to_name = {item['id']: item['function_name'] for item in batch}

        pattern = r'```json\s*(\[[\s\S]*?\])\s*```'
        match = re.search(pattern, text)
        if not match:
            array_match = re.search(r'\[\s*\{[\s\S]*?\}\s*\]', text)
            if array_match:
                match = array_match
            else:
                logger.warning("Could not find JSON array in sink batch response")
                return results

        json_text = match.group(1) if hasattr(match, 'group') and match.lastindex else match.group(0)
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse sink batch response JSON: {e}")
            return results

        if not isinstance(parsed, list):
            logger.warning(f"Sink batch response is not a list: {type(parsed)}")
            return results

        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("id", "")
            is_sink = entry.get("is_sink")
            reason = entry.get("reason", "")
            category = entry.get("category", "none")

            if entry_id not in id_to_name:
                logger.debug(f"Unknown id in sink batch response: {entry_id}")
                continue
            if not isinstance(is_sink, bool):
                if isinstance(is_sink, str):
                    is_sink = is_sink.lower() == "true"
                else:
                    logger.debug(f"Invalid is_sink for {entry_id}: {is_sink}")
                    continue
            if not isinstance(reason, str):
                reason = str(reason)
            if not isinstance(category, str):
                category = str(category) if category else "none"

            func_name = id_to_name[entry_id]
            results[func_name] = (is_sink, reason, category)

        return results

    # ─── Batching logic ──────────────────────────────────────────────────

    def _prefetch_function_bodies(self, function_names: List[str]) -> Dict[str, str]:
        bodies: Dict[str, str] = {}
        for name in function_names:
            raw = self.mcp_server.execute_tool("get_function_body", {"symbol_id": name})
            try:
                parsed = json.loads(raw)
                body = parsed.get("body", "")
                if not body and "error" in parsed:
                    body = f"[Error: {parsed['error']}]"
            except (json.JSONDecodeError, TypeError):
                body = raw if raw else "[No source available]"
            bodies[name] = body
        return bodies

    def _create_batches(self, function_names: List[str], bodies: Dict[str, str]) -> List[List[Dict[str, str]]]:
        system_prompt = self._build_batch_system_prompt()
        system_tokens = self._estimate_tokens(system_prompt)
        per_function_overhead = 50

        token_budget = self._get_input_token_budget() - system_tokens
        if token_budget < 5000:
            token_budget = 5000

        batches: List[List[Dict[str, str]]] = []
        current_batch: List[Dict[str, str]] = []
        current_tokens = 0

        for name in function_names:
            body = bodies.get(name, "[No source available]")
            func_tokens = self._estimate_tokens(body) + per_function_overhead
            short_id = uuid.uuid4().hex[:8]

            if current_batch and (
                len(current_batch) >= self.batch_size
                or current_tokens + func_tokens > token_budget
            ):
                batches.append(current_batch)
                current_batch = []
                current_tokens = 0

            current_batch.append({
                "id": short_id,
                "function_name": name,
                "body": body,
            })
            current_tokens += func_tokens

        if current_batch:
            batches.append(current_batch)

        return batches

    # ─── Batch analysis ──────────────────────────────────────────────────

    async def _analyze_batch(self, batch: List[Dict[str, str]]) -> Dict[str, Tuple[bool, str, str]]:
        system_prompt = self._build_batch_system_prompt()
        user_prompt = self._build_batch_user_prompt(batch)
        messages = [{"role": "user", "content": user_prompt}]

        await self.rate_limiter.acquire()

        try:
            response_text = await self.llm_request_fn(system_prompt, messages)
        except Exception as e:
            logger.error(f"LLM sink batch request failed: {e}")
            return {}

        if not response_text:
            logger.warning("Empty LLM sink batch response")
            return {}

        results = self._parse_batch_response(response_text, batch)

        if len(results) < len(batch):
            missing = [item['function_name'] for item in batch if item['function_name'] not in results]
            logger.debug(f"Sink batch response missing {len(missing)} functions: {missing[:3]}...")

        return results

    # ─── Single-function fallback ────────────────────────────────────────

    def _build_single_system_prompt(self) -> str:
        tool_descriptions = self.mcp_server.get_tool_descriptions()
        return f"""{_SINGLE_SYSTEM_PROMPT_PREFIX}

You have access to code navigation tools to inspect the function's source code and call relationships.

{tool_descriptions}

ANALYSIS PROCESS:
1. First, read the function body using get_function_body
2. Examine what operations it performs — does it write, execute, allocate, dispatch, or mutate state?
3. If needed, check callees (get_callees) to see if it delegates to security-sensitive operations
4. Check callers (get_callers) to understand what data flows into this function

## REQUIRED OUTPUT FORMAT

When you have gathered enough information, you MUST respond with ONLY a JSON object matching this exact schema:

```json
{{"is_sink": <boolean>, "reason": "<brief one-sentence explanation>", "category": "<sink category or none>"}}
```

### JSON Schema:
- `is_sink` (boolean, required): `true` if the function is a security-relevant sink, `false` otherwise.
- `reason` (string, required): A brief explanation (one sentence) of why you classified it this way.
- `category` (string, required): The sink category from the list above, or `"none"` if not a sink.

### Examples of correct final output:

Example 1 — function that executes a command:
```json {{"is_sink": true, "reason": "Passes a string argument to a process-spawning API, enabling command injection if input is attacker-controlled.", "category": "process_execution"}}```

Example 2 — pure computation:
```json {{"is_sink": false, "reason": "Pure arithmetic computation that does not perform any I/O or state mutation.", "category": "none"}}```

### IMPORTANT RULES:
- Your final answer MUST be a JSON object with exactly three keys: `is_sink`, `reason`, `category`
- Do NOT return a JSON array
- Do NOT omit any fields
- Do NOT include extra keys
- Wrap your final JSON in ```json ... ``` code fence
"""

    def _build_single_user_prompt(self, function_name: str) -> str:
        return f"""Analyze whether the function `{function_name}` is a security-relevant data sink.

Use the code navigation tools to inspect its source code and determine if it performs any security-sensitive operations that would be dangerous if reached by attacker-controlled data.
"""

    def _extract_json_tool_requests(self, text: str) -> List[Dict[str, Any]]:
        pattern = r'```json\s*(\{[^`]*?\})\s*```'
        matches = re.findall(pattern, text, re.DOTALL)
        results = []
        for match_text in matches:
            try:
                parsed = json.loads(match_text)
                if "tool" in parsed:
                    results.append(parsed)
            except json.JSONDecodeError:
                continue
        return results

    def _extract_final_answer(self, text: str) -> Optional[Tuple[bool, str, str]]:
        pattern = r'```json\s*(\{[^`]*?\})\s*```'
        matches = re.findall(pattern, text, re.DOTALL)
        for match_text in reversed(matches):
            try:
                parsed = json.loads(match_text)
                if self._validate_single_output_schema(parsed):
                    return (bool(parsed["is_sink"]), str(parsed["reason"]), str(parsed.get("category", "none")))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        try:
            last_brace = text.rfind('{')
            if last_brace >= 0:
                candidate = text[last_brace:]
                end_brace = candidate.find('}')
                if end_brace >= 0:
                    parsed = json.loads(candidate[:end_brace + 1])
                    if self._validate_single_output_schema(parsed):
                        return (bool(parsed["is_sink"]), str(parsed["reason"]), str(parsed.get("category", "none")))
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return None

    def _validate_single_output_schema(self, parsed: Any) -> bool:
        if not isinstance(parsed, dict):
            return False
        if "is_sink" not in parsed:
            return False
        if not isinstance(parsed["is_sink"], bool):
            return False
        if "reason" not in parsed:
            return False
        if not isinstance(parsed["reason"], str):
            return False
        if "tool" in parsed:
            return False
        return True

    async def _analyze_single_function(self, function_name: str) -> Tuple[bool, str, str]:
        system_prompt = self._build_single_system_prompt()
        messages = [{"role": "user", "content": self._build_single_user_prompt(function_name)}]

        for iteration in range(self.max_tool_iterations):
            await self.rate_limiter.acquire()

            try:
                response_text = await self.llm_request_fn(system_prompt, messages)
            except Exception as e:
                logger.error(f"LLM request failed for {function_name}: {e}")
                return (False, "LLM request failed", "none")

            if not response_text:
                logger.warning(f"Empty LLM response for {function_name}")
                return (False, "Empty LLM response", "none")

            answer = self._extract_final_answer(response_text)
            tool_requests = self._extract_json_tool_requests(response_text)
            tool_requests = [t for t in tool_requests if "tool" in t and "is_sink" not in t]

            if answer is not None and not tool_requests:
                return answer

            if not tool_requests:
                if answer is not None:
                    return answer
                logger.debug(f"No tool requests or final answer for {function_name}, defaulting to False")
                return (False, "No answer produced", "none")

            messages.append({"role": "assistant", "content": response_text})
            tool_results_parts = []
            for tool_req in tool_requests:
                tool_name = tool_req.pop("tool")
                result = self.mcp_server.execute_tool(tool_name, tool_req)
                tool_results_parts.append(f"[TOOL_RESULT: {tool_name}]\n{result}")

            tool_results_message = "\n\n".join(tool_results_parts)
            messages.append({"role": "user", "content": tool_results_message})

            if answer is not None and iteration == self.max_tool_iterations - 1:
                return answer

        logger.debug(f"Max iterations for {function_name}, sending final schema enforcement")
        await self.rate_limiter.acquire()
        messages.append({"role": "user", "content": (
            "You have reached the maximum number of tool calls. "
            "Based on everything you have seen so far, provide your FINAL answer NOW.\n\n"
            "Respond with ONLY:\n"
            '```json\n{"is_sink": <true or false>, "reason": "<brief explanation>", "category": "<category or none>"}\n```'
        )})
        try:
            response_text = await self.llm_request_fn(system_prompt, messages)
            answer = self._extract_final_answer(response_text)
            if answer is not None:
                return answer
        except Exception as e:
            logger.error(f"Final LLM request failed for {function_name}: {e}")

        logger.warning(f"Could not get valid answer for {function_name}, defaulting to False")
        return (False, "Max iterations exhausted without valid answer", "none")

    # ─── Worker and orchestration ────────────────────────────────────────

    async def _batch_worker(
        self, queue: asyncio.Queue, results: Dict[str, Tuple[bool, str, str]], total: int
    ) -> None:
        while True:
            try:
                batch = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            try:
                batch_results = await self._analyze_batch(batch)

                for func_name, (is_sink, reason, category) in batch_results.items():
                    results[func_name] = (is_sink, reason, category)
                    logger.info(f"  [{len(results)}/{total}] {func_name}: is_sink={is_sink} [{category}] ({reason})")
                    if self._on_result_callback:
                        self._on_result_callback(func_name, is_sink, reason, category)

                missing_funcs = [
                    item['function_name'] for item in batch
                    if item['function_name'] not in batch_results
                ]
                for func_name in missing_funcs:
                    try:
                        is_sink, reason, category = await self._analyze_single_function(func_name)
                        results[func_name] = (is_sink, reason, category)
                        logger.info(f"  [{len(results)}/{total}] {func_name}: is_sink={is_sink} [{category}] ({reason}) [single-retry]")
                        if self._on_result_callback:
                            self._on_result_callback(func_name, is_sink, reason, category)
                    except Exception as e:
                        logger.error(f"Single-function retry failed for {func_name}: {e}")
                        results[func_name] = (False, f"Error: {e}", "none")

            except Exception as e:
                logger.error(f"Error analyzing sink batch: {e}")
                for item in batch:
                    results[item['function_name']] = (False, f"Error: {e}", "none")
            finally:
                queue.task_done()

    async def analyze_all(self, function_names: List[str]) -> Dict[str, Tuple[bool, str, str]]:
        """
        Analyze all functions for sink classification using batched LLM calls.

        Returns:
            Dictionary mapping function_name -> (is_sink, reason, category) tuple
        """
        logger.info(f"Starting sink analysis for {len(function_names)} functions "
                    f"(workers={self.max_workers}, rate_limit={self.rate_limiter.max_requests_per_minute}/{self.rate_limiter.window_seconds}s, "
                    f"batch_size={self.batch_size})")

        logger.info(f"Pre-fetching function bodies for {len(function_names)} functions...")
        bodies = self._prefetch_function_bodies(function_names)
        logger.info(f"Pre-fetched {len(bodies)} function bodies")

        batches = self._create_batches(function_names, bodies)
        logger.info(f"Created {len(batches)} batches (avg {len(function_names)/max(len(batches),1):.1f} functions/batch)")

        queue: asyncio.Queue = asyncio.Queue()
        for batch in batches:
            queue.put_nowait(batch)

        results: Dict[str, Tuple[bool, str, str]] = {}
        total = len(function_names)
        workers = [
            asyncio.create_task(self._batch_worker(queue, results, total))
            for _ in range(min(self.max_workers, len(batches)))
        ]

        await asyncio.gather(*workers)
        self._results = results
        sink_count = sum(1 for v, _, _ in results.values() if v)
        logger.info(f"Sink analysis complete: {sink_count}/{len(results)} functions are sinks")
        return results

    def annotate_call_tree(self, call_tree: Dict[str, Any]) -> Dict[str, Any]:
        """
        Annotate a call_tree structure with is_sink fields.

        Returns:
            New dict with `is_sink`, `sink_category` added to each node.
        """
        def annotate_node(node: Dict[str, Any]) -> Dict[str, Any]:
            func_name = node.get("function", "")
            result = self._results.get(func_name, (False, "", "none"))
            is_sink, reason, category = result
            annotated = dict(node)
            annotated["is_sink"] = is_sink
            annotated["sink_category"] = category
            annotated["children"] = [annotate_node(child) for child in node.get("children", [])]
            return annotated

        root = call_tree.get("call_tree", {})
        annotated_root = annotate_node(root)

        return {
            "call_tree": annotated_root,
            "metadata": {
                **call_tree.get("metadata", {}),
                "sink_analysis": {
                    "total_functions_analyzed": len(self._results),
                    "functions_classified_as_sinks": sum(
                        1 for v, _, _ in self._results.values() if v
                    ),
                }
            }
        }


# ─── Rate limiter (shared with ExternalInputAnalyzer pattern) ────────────



# ─── Prompts ─────────────────────────────────────────────────────────────

# Shared preamble used by both batch and single-function modes
_SINK_CATEGORIES_DESCRIPTION = """
## SINK CATEGORIES

A "data sink" is any operation where attacker-controlled data reaching it could have security impact.
Classify each function into one of these categories if it is a sink, or "none" if it is not.

### 1. `process_execution`
Spawning processes, executing shell commands, interpreting code at runtime.
Examples across languages:
- Calling system(), exec(), popen(), subprocess, Runtime.exec(), os/exec
- eval(), Function() constructor, exec() (Python), dlopen/dlsym
- Script engine evaluation, template rendering with code execution
- Loading plugins, dynamic libraries, or modules from paths

### 2. `file_system_write`
Writing, creating, deleting, renaming, or modifying files and directories, changing permissions.
Examples:
- open() for writing, fwrite, writeFile, FileOutputStream, os.WriteFile
- unlink/remove/delete file operations
- chmod, chown, setxattr, modifying file metadata
- Creating symlinks or hard links

### 3. `network_output`
Sending data over a network connection — HTTP responses, socket writes, DNS lookups with attacker-controlled hostnames.
Examples:
- send(), sendto(), write() on sockets, URLSession, HttpURLConnection
- HTTP response body/header construction
- WebSocket message sends
- SMTP, DNS, or other protocol output

### 4. `database_write`
Executing queries that modify persistent storage — SQL INSERT/UPDATE/DELETE, NoSQL mutations, key-value store writes.
Examples:
- SQL query execution (especially with string interpolation)
- ORM save/update/delete operations
- Key-value store put/set operations (Redis, LevelDB, NSUserDefaults, SharedPreferences)
- Core Data / Realm / Room saves

### 5. `memory_operation`
Buffer copies, array/pointer indexing, or memory allocation where the size, index, or source is attacker-influenced.
Examples:
- memcpy, memmove, strncpy with attacker-controlled length
- Array indexing with unchecked external index
- malloc/calloc/realloc with attacker-controlled size
- Buffer reads/writes with computed offsets

### 6. `deserialization`
Deserializing complex objects from data that may be attacker-influenced — type confusion, code execution, gadget chains.
Examples:
- NSKeyedUnarchiver, Java ObjectInputStream, pickle.load, JSON.parse into typed objects
- Protocol buffer / flatbuffer parsing from untrusted sources
- YAML/XML parsing with object instantiation (YAML.load, XMLDecoder)
- Custom binary format parsing

### 7. `authentication_authorization`
Making access control decisions — verifying credentials, checking permissions, validating tokens.
Examples:
- Password comparison / hash verification
- Token/JWT validation and claims extraction
- Entitlement/capability checks
- Role/permission gate functions
- Session creation or validation

### 8. `cryptographic_operation`
Cryptographic operations where attacker-controlled input affects key material, plaintext, IV, or algorithm selection.
Examples:
- Key derivation (PBKDF2, scrypt, HKDF) with external input
- Encryption/decryption with attacker-influenced parameters
- Digital signature creation or verification
- HMAC computation, hash generation for security purposes
- Random number generation seeding

### 9. `system_state_modification`
Changing system-wide or process-wide state — setting system time, modifying system configuration, changing locale/timezone.
Examples:
- settimeofday, clock_settime, SetSystemTime
- System configuration writes (registry, plist, sysctl)
- Environment variable modification for child processes
- Kernel parameter changes

### 10. `ipc_output`
Sending data to other processes via inter-process communication mechanisms.
Examples:
- XPC message replies, D-Bus signal emission, Windows named pipes
- Shared memory writes
- Unix domain socket sends
- Android Binder/Intent sends, ContentProvider mutations
- POSIX message queue writes

### 11. `logging_with_user_data`
Logging or diagnostic output where attacker-controlled strings are interpolated — format string vulnerabilities, log injection.
Examples:
- printf/NSLog/syslog with format strings from external data
- Log frameworks where user input flows into log messages without sanitization
- Crash report / analytics payloads containing user-controlled data
- Audit trail writes with unsanitized input

### 12. `dynamic_dispatch`
Method resolution, class loading, or function invocation determined by attacker-controlled strings.
Examples:
- NSSelectorFromString / performSelector with external string
- Class.forName / reflection-based instantiation
- getattr() / importlib with user-controlled module names
- Dynamic method dispatch based on command strings from IPC

### 13. `url_path_construction`
Building URLs, file paths, or resource identifiers by concatenating or interpolating attacker-controlled data.
Examples:
- String concatenation to form file paths (path traversal)
- URL construction with user-controlled components (SSRF)
- Resource identifier building (scheme://host/path from external data)
- Template-based path construction

### 14. `query_construction`
Building query strings for databases, search engines, or directory services from external data.
Examples:
- SQL string concatenation or interpolation (SQL injection)
- LDAP query construction
- XPath/XQuery building
- GraphQL query string assembly
- OS command string building (command injection)
- Regular expression construction from user input (ReDoS)

### 15. `markup_generation`
Generating HTML, XML, JavaScript, or other interpreted markup from external data — XSS, injection.
Examples:
- HTML template rendering with user-controlled variables
- JavaScript code generation / embedding
- XML document construction with external values
- Email body construction with user content

### 16. `privilege_boundary`
Functions that transition between privilege levels — setuid, sandbox escape, entitlement-gated operations.
Examples:
- setuid/setgid/seteuid calls
- Sandbox policy evaluation or escape
- Entitlement verification before privileged operation
- Capability acquisition or dropping
- Switching between security contexts

### 17. `resource_allocation`
Allocating system resources (threads, file descriptors, network connections, semaphores) where the count or size is attacker-influenced — denial of service.
Examples:
- Thread/goroutine/task creation in loops controlled by external input
- File descriptor or socket allocation without bounds
- Semaphore/lock creation with attacker-controlled count
- Timer/alarm registration in unbounded quantities

### 18. `notification_broadcast`
Broadcasting system-wide or cross-process notifications/signals that affect other components.
Examples:
- CFNotificationCenterPostNotification, NSNotificationCenter
- Android broadcast intents
- D-Bus signals, Windows messages
- POSIX signals to other processes (kill/sigqueue)
"""

_BATCH_SYSTEM_PROMPT = f"""You are a security-focused code analyst. Your task is to determine whether each given function is a SECURITY-RELEVANT DATA SINK — an operation where attacker-controlled data reaching it could have security impact.

{_SINK_CATEGORIES_DESCRIPTION}

## CLASSIFICATION GUIDANCE

### HIGH PRIORITY — classify as is_sink=true:
- Functions that directly call OS/system APIs to modify state (write files, execute processes, set system time)
- Functions that construct queries, commands, URLs, or paths from parameters
- Functions that perform deserialization of complex objects
- Functions that make authentication/authorization decisions
- Functions that send data over network or IPC boundaries
- Functions that allocate resources based on input-controlled sizes
- Functions that use dynamic dispatch based on string parameters

### LOW PRIORITY — classify as is_sink=false:
- Pure computation functions (math, string formatting, data transformation)
- Functions that only READ data without side effects
- Internal state management with no external impact (updating a UI label, toggling a boolean flag)
- Getter/accessor methods returning internal state
- Logging calls where only static/constant strings are logged (no user-controlled content)
- Factory/builder methods that construct internal objects from trusted parameters
- Event registration/deregistration with no immediate side effect

### KEY DISTINCTION:
The critical question is: "If attacker-controlled data reaches this function, could it cause harm?"
A function that writes to a file IS a sink (file content corruption, arbitrary file write).
A function that reads a file is NOT a sink (it's a source, not a sink).
Focus on WRITE/EXECUTE/MUTATE operations, not READ operations.

## REQUIRED OUTPUT FORMAT

You will be given multiple functions to analyze in a single request. Each function has a unique ID (uuid).

You MUST respond with a JSON array containing one result object per function, in any order. Each object MUST have these exact fields:
- `id` (string, required): The uuid of the function (copied exactly from the input)
- `is_sink` (boolean, required): `true` if the function is a security-relevant sink, `false` otherwise
- `reason` (string, required): A brief one-sentence explanation
- `category` (string, required): One of the sink category identifiers listed above, or `"none"` if not a sink

Wrap your response in a single ```json ... ``` code fence containing the array.

### Example response for a batch of 3 functions:

```json
[
  {{"id": "a1b2c3d4", "is_sink": true, "reason": "Calls settimeofday() with a value extracted from an IPC dictionary, modifying system-wide time.", "category": "system_state_modification"}},
  {{"id": "e5f6g7h8", "is_sink": false, "reason": "Pure Kalman filter computation that updates internal state variables without any I/O.", "category": "none"}},
  {{"id": "i9j0k1l2", "is_sink": true, "reason": "Constructs an SQL query by string interpolation with a user-provided search term.", "category": "query_construction"}}
]
```

### IMPORTANT RULES:
- You MUST return exactly ONE result for EACH function provided (match by `id`)
- Your response MUST be a JSON array (not a single object)
- Each element MUST have all four fields: `id`, `is_sink`, `reason`, `category`
- The `id` field MUST match exactly one of the input function IDs
- The `category` field MUST be one of the 18 defined categories or `"none"`
- Do NOT skip any functions — provide a result for every single one
"""

_SINGLE_SYSTEM_PROMPT_PREFIX = f"""You are a security-focused code analyst. Your task is to determine whether a given function is a SECURITY-RELEVANT DATA SINK — an operation where attacker-controlled data reaching it could have security impact.

{_SINK_CATEGORIES_DESCRIPTION}

## CLASSIFICATION GUIDANCE

### HIGH PRIORITY — classify as is_sink=true:
- Functions that directly call OS/system APIs to modify state
- Functions that construct queries, commands, URLs, or paths from parameters
- Functions that perform deserialization of complex objects
- Functions that make authentication/authorization decisions
- Functions that send data over network or IPC boundaries
- Functions that allocate resources based on input-controlled sizes
- Functions that use dynamic dispatch based on string parameters

### LOW PRIORITY — classify as is_sink=false:
- Pure computation functions with no side effects
- Functions that only READ data
- Internal state management with no external impact
- Getter/accessor methods
- Logging with only static/constant strings
- Factory/builder methods constructing from trusted parameters

### KEY DISTINCTION:
"If attacker-controlled data reaches this function, could it cause harm?"
Focus on WRITE/EXECUTE/MUTATE operations, not READ operations.
"""
