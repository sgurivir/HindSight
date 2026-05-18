#!/usr/bin/env python3
"""
External Input Analyzer — async/await parallel LLM analysis to determine
whether functions accept external (untrusted) input.

Sends up to 8 functions per LLM call (batched mode) to reduce total request
count and stay within rate limits. Each function in a batch is assigned a UUID
so responses can be reliably correlated.

Uses cooperative multitasking with asyncio to parallelize LLM requests
while respecting a configurable rate limit.

Output: call_tree_with_sources.json — same schema as call_tree.json with
an added `ext_input` boolean field on each node.
"""

import asyncio
import json
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.constants import (
    EXTERNAL_INPUT_RATE_LIMIT,
    EXTERNAL_INPUT_DEFAULT_WORKERS,
    EXTERNAL_INPUT_MAX_TOOL_ITERATIONS,
    EXTERNAL_INPUT_BATCH_SIZE,
    EXTERNAL_INPUT_TOKEN_BUDGET_RATIO,
    EXTERNAL_INPUT_CHARS_PER_TOKEN,
    LLM_PROVIDER_RATE_WINDOW_SECONDS,
)
from ..core.async_infra import RateLimiter
from ..core.mcp_tools.code_navigation_server import CodeNavigationServer
from ..utils.log_util import get_logger

logger = get_logger(__name__)


class ExternalInputAnalyzer:
    """
    Async analyzer that uses LLM + MCP code navigation tools to determine
    whether each function in a call tree accepts external input.

    Operates in batched mode: sends up to EXTERNAL_INPUT_BATCH_SIZE functions
    per LLM request, with pre-fetched function bodies included inline.
    Each function is tagged with a UUID for reliable response correlation.
    """

    def __init__(
        self,
        mcp_server: CodeNavigationServer,
        llm_request_fn: Callable,
        rate_limit: int = EXTERNAL_INPUT_RATE_LIMIT,
        max_workers: int = EXTERNAL_INPUT_DEFAULT_WORKERS,
        max_tool_iterations: int = EXTERNAL_INPUT_MAX_TOOL_ITERATIONS,
        batch_size: int = EXTERNAL_INPUT_BATCH_SIZE,
        context_window: int = 200_000,
        on_result_callback: Optional[Callable[[str, bool, str], None]] = None,
    ):
        """
        Args:
            mcp_server: CodeNavigationServer instance for tool execution
            llm_request_fn: Async callable(system_prompt, messages) -> str
                            that sends a request to the LLM and returns response text
            rate_limit: Maximum LLM requests per minute
            max_workers: Number of parallel workers
            max_tool_iterations: Max tool-call rounds per function analysis (single-function fallback)
            batch_size: Max functions per batched LLM call (default 8)
            context_window: Model context window in tokens (used for budget calculation)
            on_result_callback: Optional callback(func_name, ext_input, reason) called after each function
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
        self._results: Dict[str, Tuple[bool, str]] = {}  # func_name -> (ext_input, reason)

    # ─── Token budget estimation ─────────────────────────────────────────

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate: 1 token ≈ 4 chars."""
        return len(text) // EXTERNAL_INPUT_CHARS_PER_TOKEN

    def _get_input_token_budget(self) -> int:
        """Max tokens we can spend on prompt input (system + user messages)."""
        return int(self.context_window * EXTERNAL_INPUT_TOKEN_BUDGET_RATIO)

    # ─── Batch prompt building ───────────────────────────────────────────

    def _build_batch_system_prompt(self) -> str:
        return """You are a security-focused code analyst. Your task is to determine whether each given function accepts EXTERNAL INPUT — input that originates from outside the application's trust boundary.

External input sources include:
- User input (UI fields, command-line arguments, stdin)
- Network data (HTTP requests, WebSocket messages, RPC calls, API responses from external services)
- File system data read from user-controlled paths
- Inter-process communication from untrusted processes
- Environment variables that can be user-controlled
- Database fields that store user-provided data (indirect external input)

A function accepts external input if:
1. It DIRECTLY receives external input as a parameter (e.g. request handler, delegate callback with user data)
2. It DIRECTLY reads from an external source (e.g. reads a network socket, reads stdin)

A function does NOT accept external input if:
- It only processes data passed from internal callers where the data was already validated
- It is a pure computation function with no I/O
- Its parameters are all internal/trusted values

## PRIORITY GUIDANCE

Focus on HIGH-PRIORITY external inputs that represent real attack surfaces. Deprioritize (classify as ext_input=false) inputs that are framework-constrained and carry no attacker-controlled content.

### HIGH PRIORITY — classify as ext_input=true:
- Network response/request handlers (attacker-controlled server or client data)
- URL scheme / deep link / universal link handlers (attacker-crafted URLs)
- File/document import handlers (attacker-crafted file content)
- Clipboard/pasteboard reads (cross-app data injection)
- Deserialization of complex objects from persistence (type confusion, code execution)
- IPC/XPC/RPC boundaries (privilege escalation vectors)
- Push notification payload handlers (server-controlled content)
- Free-form text input that flows into queries, URLs, commands, or file paths (injection)
- WebView/JavaScript bridge callbacks (web-to-native attacks)
- Voice assistant / intent handlers with parameters (crafted invocations)
- Share extension / app extension input contexts (untrusted app data)
- Activity continuation / handoff payloads (crafted activity data)

### LOW PRIORITY — classify as ext_input=false (deprioritize):
- Framework-constrained UI callbacks with no user content:
  - Selection callbacks where the index/position is bounded by the framework (e.g. list/table row selection, tab selection, page indices)
  - Toggle/switch callbacks that only carry a boolean value
  - Segment controls that only carry a bounded integer index
  - Slider/stepper callbacks with bounded numeric range
- Lifecycle callbacks with no meaningful parameters (app launch, view appear, layout passes)
- Internal notification/event observer callbacks within the same process
- Gesture recognizers that carry only position/state (no user-supplied content)
- Pure UI configuration/layout methods receiving internal model data
- Simple reads of primitive types (Bool, Int, bounded enum) from app-managed local storage

### KEY DISTINCTION:
The critical question is: "Can an attacker control the CONTENT flowing through this input?" A bounded index selected from a list the app itself populates is not attacker-controlled. A URL opened from an external source IS attacker-controlled. Focus on content, not on whether a human triggered the action.

## REQUIRED OUTPUT FORMAT

You will be given multiple functions to analyze in a single request. Each function has a unique ID (uuid).

You MUST respond with a JSON array containing one result object per function, in any order. Each object MUST have these exact fields:
- `id` (string, required): The uuid of the function (copied exactly from the input)
- `ext_input` (boolean, required): `true` if the function accepts external input, `false` otherwise
- `reason` (string, required): A brief one-sentence explanation

Wrap your response in a single ```json ... ``` code fence containing the array.

### Example response for a batch of 3 functions:

```json
[
  {"id": "a1b2c3d4", "ext_input": true, "reason": "Directly receives HTTP request body as parameter from URL route handler."},
  {"id": "e5f6g7h8", "ext_input": false, "reason": "Pure computation function that formats a date string from internal calendar data."},
  {"id": "i9j0k1l2", "ext_input": false, "reason": "Framework-constrained table row selection callback; index is bounded by data source, no attacker-controlled content flows through."}
]
```

### IMPORTANT RULES:
- You MUST return exactly ONE result for EACH function provided (match by `id`)
- Your response MUST be a JSON array (not a single object)
- Each element MUST have all three fields: `id`, `ext_input`, `reason`
- The `id` field MUST match exactly one of the input function IDs
- Do NOT skip any functions — provide a result for every single one
"""

    def _build_batch_user_prompt(self, batch: List[Dict[str, str]]) -> str:
        """Build the user prompt for a batch of functions.

        Args:
            batch: List of dicts with keys: id, function_name, body
        """
        parts = [
            "Analyze the following functions and determine whether each accepts external (untrusted) input.\n"
            "For each function, examine its source code, parameters, and what it does.\n\n"
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

    def _parse_batch_response(self, text: str, batch: List[Dict[str, str]]) -> Dict[str, Tuple[bool, str]]:
        """Parse the LLM batch response and correlate results by UUID.

        Returns dict mapping function_name -> (ext_input, reason).
        Functions not found in the response are not included.
        """
        results: Dict[str, Tuple[bool, str]] = {}

        # Build id -> function_name lookup
        id_to_name = {item['id']: item['function_name'] for item in batch}

        # Extract JSON array from code fence
        pattern = r'```json\s*(\[[\s\S]*?\])\s*```'
        match = re.search(pattern, text)
        if not match:
            # Try without code fence — look for a JSON array
            array_match = re.search(r'\[\s*\{[\s\S]*?\}\s*\]', text)
            if array_match:
                match = array_match
            else:
                logger.warning("Could not find JSON array in batch response")
                return results

        json_text = match.group(1) if hasattr(match, 'group') and match.lastindex else match.group(0)
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse batch response JSON: {e}")
            return results

        if not isinstance(parsed, list):
            logger.warning(f"Batch response is not a list: {type(parsed)}")
            return results

        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("id", "")
            ext_input = entry.get("ext_input")
            reason = entry.get("reason", "")

            if entry_id not in id_to_name:
                logger.debug(f"Unknown id in batch response: {entry_id}")
                continue
            if not isinstance(ext_input, bool):
                # Try to coerce string "true"/"false"
                if isinstance(ext_input, str):
                    ext_input = ext_input.lower() == "true"
                else:
                    logger.debug(f"Invalid ext_input for {entry_id}: {ext_input}")
                    continue
            if not isinstance(reason, str):
                reason = str(reason)

            func_name = id_to_name[entry_id]
            results[func_name] = (ext_input, reason)

        return results

    # ─── Batching logic ──────────────────────────────────────────────────

    def _prefetch_function_bodies(self, function_names: List[str]) -> Dict[str, Optional[str]]:
        """Pre-fetch function bodies from the MCP server for all functions.

        Returns a dict mapping function_name -> body text, where None means
        the source code could not be retrieved (symbol not found, file missing, etc.).
        """
        bodies: Dict[str, Optional[str]] = {}
        for name in function_names:
            raw = self.mcp_server.execute_tool("get_function_body", {"symbol_id": name})
            try:
                parsed = json.loads(raw)
                body = parsed.get("body", "") or None
            except (json.JSONDecodeError, TypeError):
                body = raw if raw else None
            bodies[name] = body
        return bodies

    def _create_batches(self, function_names: List[str], bodies: Dict[str, Optional[str]]) -> List[List[Dict[str, str]]]:
        """Split functions into batches respecting both batch_size and token budget.

        Each batch item is: {id, function_name, body}
        Only includes functions that have available source code (non-None bodies).
        """
        system_prompt = self._build_batch_system_prompt()
        system_tokens = self._estimate_tokens(system_prompt)
        # Reserve tokens for the surrounding prompt structure per function
        per_function_overhead = 50  # tokens for headers, separators, etc.

        token_budget = self._get_input_token_budget() - system_tokens
        if token_budget < 5000:
            token_budget = 5000  # absolute minimum

        batches: List[List[Dict[str, str]]] = []
        current_batch: List[Dict[str, str]] = []
        current_tokens = 0

        for name in function_names:
            body = bodies.get(name)
            if body is None:
                continue
            func_tokens = self._estimate_tokens(body) + per_function_overhead
            short_id = uuid.uuid4().hex[:8]

            # If adding this function would exceed budget, finalize current batch
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

    async def _analyze_batch(self, batch: List[Dict[str, str]]) -> Dict[str, Tuple[bool, str]]:
        """Analyze a batch of functions in a single LLM call.

        Returns dict mapping function_name -> (ext_input, reason).
        Functions that couldn't be parsed get no entry (will be retried individually).
        """
        system_prompt = self._build_batch_system_prompt()
        user_prompt = self._build_batch_user_prompt(batch)
        messages = [{"role": "user", "content": user_prompt}]

        await self.rate_limiter.acquire()

        try:
            response_text = await self.llm_request_fn(system_prompt, messages)
        except Exception as e:
            logger.error(f"LLM batch request failed: {e}")
            return {}

        if not response_text:
            logger.warning("Empty LLM batch response")
            return {}

        results = self._parse_batch_response(response_text, batch)

        # If we got fewer results than expected, log it
        if len(results) < len(batch):
            missing = [item['function_name'] for item in batch if item['function_name'] not in results]
            logger.debug(f"Batch response missing {len(missing)} functions: {missing[:3]}...")

        return results

    # ─── Single-function fallback (for retry of batch failures) ──────────

    def _build_single_system_prompt(self) -> str:
        tool_descriptions = self.mcp_server.get_tool_descriptions()
        return f"""You are a security-focused code analyst. Your task is to determine whether a given function accepts EXTERNAL INPUT — input that originates from outside the application's trust boundary.

External input sources include:
- User input (UI fields, command-line arguments, stdin)
- Network data (HTTP requests, WebSocket messages, RPC calls, API responses from external services)
- File system data read from user-controlled paths
- Inter-process communication from untrusted processes
- Environment variables that can be user-controlled
- Database fields that store user-provided data (indirect external input)

A function accepts external input if:
1. It DIRECTLY receives external input as a parameter (e.g. request handler, delegate callback with user data)
2. It DIRECTLY reads from an external source (e.g. reads a network socket, reads stdin)

A function does NOT accept external input if:
- It only processes data passed from internal callers where the data was already validated
- It is a pure computation function with no I/O
- Its parameters are all internal/trusted values

## PRIORITY GUIDANCE

Focus on HIGH-PRIORITY external inputs that represent real attack surfaces. Deprioritize (classify as ext_input=false) inputs that are framework-constrained and carry no attacker-controlled content.

### HIGH PRIORITY — classify as ext_input=true:
- Network response/request handlers (attacker-controlled server or client data)
- URL scheme / deep link / universal link handlers (attacker-crafted URLs)
- File/document import handlers (attacker-crafted file content)
- Clipboard/pasteboard reads (cross-app data injection)
- Deserialization of complex objects from persistence (type confusion, code execution)
- IPC/XPC/RPC boundaries (privilege escalation vectors)
- Push notification payload handlers (server-controlled content)
- Free-form text input that flows into queries, URLs, commands, or file paths (injection)
- WebView/JavaScript bridge callbacks (web-to-native attacks)
- Voice assistant / intent handlers with parameters (crafted invocations)
- Share extension / app extension input contexts (untrusted app data)
- Activity continuation / handoff payloads (crafted activity data)

### LOW PRIORITY — classify as ext_input=false (deprioritize):
- Framework-constrained UI callbacks with no user content:
  - Selection callbacks where the index/position is bounded by the framework (e.g. list/table row selection, tab selection, page indices)
  - Toggle/switch callbacks that only carry a boolean value
  - Segment controls that only carry a bounded integer index
  - Slider/stepper callbacks with bounded numeric range
- Lifecycle callbacks with no meaningful parameters (app launch, view appear, layout passes)
- Internal notification/event observer callbacks within the same process
- Gesture recognizers that carry only position/state (no user-supplied content)
- Pure UI configuration/layout methods receiving internal model data
- Simple reads of primitive types (Bool, Int, bounded enum) from app-managed local storage

### KEY DISTINCTION:
The critical question is: "Can an attacker control the CONTENT flowing through this input?" A bounded index selected from a list the app itself populates is not attacker-controlled. A URL opened from an external source IS attacker-controlled. Focus on content, not on whether a human triggered the action.

You have access to code navigation tools to inspect the function's source code and call relationships.

{tool_descriptions}

ANALYSIS PROCESS:
1. First, read the function body using get_function_body
2. Examine its parameters and what it does with them
3. If needed, check callers (get_callers) to understand the call context
4. Look at callees if the function delegates to I/O operations

## REQUIRED OUTPUT FORMAT

When you have gathered enough information, you MUST respond with ONLY a JSON object matching this exact schema:

```json
{{"ext_input": <boolean>, "reason": "<brief one-sentence explanation>"}}
```

### JSON Schema:
- `ext_input` (boolean, required): `true` if the function accepts external input, `false` otherwise.
- `reason` (string, required): A brief explanation (one sentence) of why you classified it this way.

### Examples of correct final output:

Example 1 — function that handles HTTP requests:
```json {{"ext_input": true, "reason": "Directly receives HTTP request body as parameter from URL route handler."}}```

Example 2 — internal helper with no I/O:
```json {{"ext_input": false, "reason": "Pure computation function that formats a date string from internal calendar data."}}```

Example 3 — framework-constrained UI callback:
```json {{"ext_input": false, "reason": "Framework-constrained list selection callback; index is bounded by data source, no attacker-controlled content."}}```

### IMPORTANT RULES:
- Your final answer MUST be a JSON object with exactly two keys: `ext_input` and `reason`
- Do NOT return a JSON array
- Do NOT omit the `reason` field
- Do NOT include extra keys beyond `ext_input` and `reason`
- Wrap your final JSON in ```json ... ``` code fence
"""

    def _build_single_user_prompt(self, function_name: str) -> str:
        return f"""Analyze whether the function `{function_name}` accepts external (untrusted) input.

Use the code navigation tools to inspect its source code and determine if it directly receives or reads external input.
"""

    def _extract_json_tool_requests(self, text: str) -> List[Dict[str, Any]]:
        """Extract JSON tool requests from LLM response text."""
        pattern = r'```json\s*(\{[^`]*?\})\s*```'
        matches = re.findall(pattern, text, re.DOTALL)
        results = []
        for match in matches:
            try:
                parsed = json.loads(match)
                if "tool" in parsed:
                    results.append(parsed)
            except json.JSONDecodeError:
                continue
        return results

    def _extract_final_answer(self, text: str) -> Optional[Tuple[bool, str]]:
        """Extract the final ext_input verdict from LLM response."""
        pattern = r'```json\s*(\{[^`]*?\})\s*```'
        matches = re.findall(pattern, text, re.DOTALL)
        for match in reversed(matches):
            try:
                parsed = json.loads(match)
                if self._validate_single_output_schema(parsed):
                    return (bool(parsed["ext_input"]), str(parsed["reason"]))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        # Also try unformatted JSON at end of response
        try:
            last_brace = text.rfind('{')
            if last_brace >= 0:
                candidate = text[last_brace:]
                end_brace = candidate.find('}')
                if end_brace >= 0:
                    parsed = json.loads(candidate[:end_brace + 1])
                    if self._validate_single_output_schema(parsed):
                        return (bool(parsed["ext_input"]), str(parsed["reason"]))
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return None

    def _validate_single_output_schema(self, parsed: Any) -> bool:
        """Validate that parsed JSON matches the required single-function output schema."""
        if not isinstance(parsed, dict):
            return False
        if "ext_input" not in parsed:
            return False
        if not isinstance(parsed["ext_input"], bool):
            return False
        if "reason" not in parsed:
            return False
        if not isinstance(parsed["reason"], str):
            return False
        if "tool" in parsed:
            return False
        return True

    async def _analyze_single_function(self, function_name: str) -> Tuple[bool, str]:
        """Analyze a single function using iterative LLM + tool calls (fallback mode)."""
        system_prompt = self._build_single_system_prompt()
        messages = [{"role": "user", "content": self._build_single_user_prompt(function_name)}]

        for iteration in range(self.max_tool_iterations):
            await self.rate_limiter.acquire()

            try:
                response_text = await self.llm_request_fn(system_prompt, messages)
            except Exception as e:
                logger.error(f"LLM request failed for {function_name}: {e}")
                return (False, "LLM request failed")

            if not response_text:
                logger.warning(f"Empty LLM response for {function_name}")
                return (False, "Empty LLM response")

            answer = self._extract_final_answer(response_text)
            tool_requests = self._extract_json_tool_requests(response_text)
            tool_requests = [t for t in tool_requests if "tool" in t and "ext_input" not in t]

            if answer is not None and not tool_requests:
                return answer

            if not tool_requests:
                if answer is not None:
                    return answer
                logger.debug(f"No tool requests or final answer for {function_name}, defaulting to False")
                return (False, "No answer produced")

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

        # Max iterations exhausted
        logger.debug(f"Max iterations for {function_name}, sending final schema enforcement")
        await self.rate_limiter.acquire()
        messages.append({"role": "user", "content": (
            "You have reached the maximum number of tool calls. "
            "Based on everything you have seen so far, provide your FINAL answer NOW.\n\n"
            "Respond with ONLY:\n"
            '```json\n{"ext_input": <true or false>, "reason": "<brief explanation>"}\n```'
        )})
        try:
            response_text = await self.llm_request_fn(system_prompt, messages)
            answer = self._extract_final_answer(response_text)
            if answer is not None:
                return answer
        except Exception as e:
            logger.error(f"Final LLM request failed for {function_name}: {e}")

        logger.warning(f"Could not get valid answer for {function_name}, defaulting to False")
        return (False, "Max iterations exhausted without valid answer")

    # ─── Worker and orchestration ────────────────────────────────────────

    async def _batch_worker(
        self, queue: asyncio.Queue, results: Dict[str, Tuple[bool, str]], total: int
    ) -> None:
        """Worker that pulls batches from the queue and analyzes them."""
        while True:
            try:
                batch = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            try:
                batch_results = await self._analyze_batch(batch)

                # Record successful results
                for func_name, (ext_input, reason) in batch_results.items():
                    results[func_name] = (ext_input, reason)
                    logger.info(f"  [{len(results)}/{total}] {func_name}: ext_input={ext_input} ({reason})")
                    if self._on_result_callback:
                        self._on_result_callback(func_name, ext_input, reason)

                # Retry missing functions individually (single-function fallback)
                missing_funcs = [
                    item['function_name'] for item in batch
                    if item['function_name'] not in batch_results
                ]
                for func_name in missing_funcs:
                    try:
                        ext_input, reason = await self._analyze_single_function(func_name)
                        results[func_name] = (ext_input, reason)
                        logger.info(f"  [{len(results)}/{total}] {func_name}: ext_input={ext_input} ({reason}) [single-retry]")
                        if self._on_result_callback:
                            self._on_result_callback(func_name, ext_input, reason)
                    except Exception as e:
                        logger.error(f"Single-function retry failed for {func_name}: {e}")
                        results[func_name] = (False, f"Error: {e}")

            except Exception as e:
                logger.error(f"Error analyzing batch: {e}")
                for item in batch:
                    results[item['function_name']] = (False, f"Error: {e}")
            finally:
                queue.task_done()

    async def analyze_all(self, function_names: List[str]) -> Dict[str, Tuple[bool, str]]:
        """
        Analyze all functions using batched LLM calls.

        Pre-fetches all function bodies, groups into batches respecting
        token budget and batch_size, then processes batches in parallel.

        Args:
            function_names: List of function names to analyze

        Returns:
            Dictionary mapping function_name -> (ext_input, reason) tuple
        """
        logger.info(f"Starting external input analysis for {len(function_names)} functions "
                    f"(workers={self.max_workers}, rate_limit={self.rate_limiter.max_requests_per_minute}/{self.rate_limiter.window_seconds}s, "
                    f"batch_size={self.batch_size})")

        # Pre-fetch all function bodies
        logger.info(f"Pre-fetching function bodies for {len(function_names)} functions...")
        bodies = self._prefetch_function_bodies(function_names)
        logger.info(f"Pre-fetched {len(bodies)} function bodies")

        # Immediately resolve functions with no source — skip LLM for these
        results: Dict[str, Tuple[bool, str]] = {}
        for name in function_names:
            if bodies.get(name) is None:
                results[name] = (False, "No source code available in analyzed repo")
                if self._on_result_callback:
                    self._on_result_callback(name, False, "No source code available in analyzed repo")

        analyzable_count = len(function_names) - len(results)
        if results:
            logger.info(f"Skipped {len(results)} functions with no source code; "
                        f"analyzing {analyzable_count} functions via LLM")

        # Create batches respecting token budget (only functions with source)
        batches = self._create_batches(function_names, bodies)
        logger.info(f"Created {len(batches)} batches (avg {analyzable_count/max(len(batches),1):.1f} functions/batch)")

        # Queue up batches
        queue: asyncio.Queue = asyncio.Queue()
        for batch in batches:
            queue.put_nowait(batch)

        total = len(function_names)
        workers = [
            asyncio.create_task(self._batch_worker(queue, results, total))
            for _ in range(min(self.max_workers, len(batches)))
        ]

        await asyncio.gather(*workers)
        self._results = results
        logger.info(f"External input analysis complete: "
                    f"{sum(1 for v, _ in results.values() if v)}/{len(results)} functions accept external input")
        return results

    def annotate_call_tree(self, call_tree: Dict[str, Any]) -> Dict[str, Any]:
        """
        Annotate a call_tree.json structure with ext_input fields.

        Args:
            call_tree: The call tree dict (output of CallTreeGenerator.generate_call_tree())

        Returns:
            New dict with same structure but `ext_input` and `ext_input_reason` added to each node
        """
        def annotate_node(node: Dict[str, Any]) -> Dict[str, Any]:
            func_name = node.get("function", "")
            result = self._results.get(func_name, (False, ""))
            ext_input, reason = result
            annotated = {
                "function": func_name,
                "location": node.get("location", []),
                "ext_input": ext_input,
                "children": [annotate_node(child) for child in node.get("children", [])]
            }
            return annotated

        root = call_tree.get("call_tree", {})
        annotated_root = annotate_node(root)

        return {
            "call_tree": annotated_root,
            "metadata": {
                **call_tree.get("metadata", {}),
                "external_input_analysis": {
                    "total_functions_analyzed": len(self._results),
                    "functions_with_external_input": sum(
                        1 for v, _ in self._results.values() if v
                    ),
                }
            }
        }
