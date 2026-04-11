# Credential Management

This document explains how authentication tokens are acquired and automatically refreshed when running the `code_analyzer` tool.

## Table of Contents

1. [Quick Start](#quick-start-get-appleconnect-token)
2. [Token Types and Duration](#token-types-and-duration)
3. [Automatic Token Refresh](#automatic-token-refresh)
4. [Authentication Flow](#authentication-flow)
5. [Configuration Examples](#configuration-examples)
6. [Implementation Details](#implementation-details)
7. [Troubleshooting](#troubleshooting)

---

## Quick Start: Get AppleConnect Token

To manually retrieve an AppleConnect OAuth token from the terminal, run:

```bash
/usr/local/bin/appleconnect getToken \
    -C hvys3fcwcteqrvw3qzkvtk86viuoqv \
    --token-type=oauth \
    --interactivity-type=none \
    -E prod \
    -G pkce \
    -o openid,dsid,accountname,profile,groups
```

The token will be printed to stdout. You can capture it in a variable:

```bash
TOKEN=$(/usr/local/bin/appleconnect getToken -C hvys3fcwcteqrvw3qzkvtk86viuoqv --token-type=oauth --interactivity-type=none -E prod -G pkce -o openid,dsid,accountname,profile,groups | tail -1 | awk '{print $NF}')
echo $TOKEN
```

### Getting a Refresh Token

To get a **refresh token** (which can be used to obtain new access tokens without re-authenticating), add `offline_access` to the OAuth scopes:

```bash
/usr/local/bin/appleconnect getToken \
    -C hvys3fcwcteqrvw3qzkvtk86viuoqv \
    --token-type=oauth \
    --interactivity-type=none \
    -E prod \
    -G pkce \
    -o openid,dsid,accountname,profile,groups,offline_access
```

This returns three tokens:
- `oauth-access-token` - The access token (short-lived, ~30 minutes)
- `oauth-id-token` - The ID token with user information
- `oauth-refresh-token` - The refresh token (longer-lived, use to get new access tokens)

### Using a Refresh Token

Once you have a refresh token, you can use it to get a new access token without interactive authentication:

```bash
/usr/local/bin/appleconnect getToken \
    -C hvys3fcwcteqrvw3qzkvtk86viuoqv \
    --token-type=oauth \
    -E prod \
    -G pkce \
    -R <your-refresh-token>
```

### All Available Options

```
Usage: appleconnect getToken [-I appID] [options ...]
   -a, --account                  AppleConnect account name
   -E, --environment              Environment: prod, uat
   -I, --appID                    Application ID (required for non-OAuth tokens)
   -k, --keytab                   Path to a keytab file
   -i, --use-identity             Identity type: yubikey, touchId, delegated
   -n, --interactivity-type       Interactivity type: cli, gui, none
   -t, --token-type               Type of token: oauth, daw, saml, default
   -N, --nonce                    Randomly generated, unique per client session
   -C, --oauth-client-ID          OAuth client ID
   -S, --oauth-client-secret      OAuth client secret
   -G, --oauth-grant-type         OAuth grant type: code, client-creds, pkce
   -o, --oauth-scope              OAuth scope
   -A, --oauth-audience           OAuth audience
   -R, --oauth-refresh-token      OAuth refresh token
   -u, --oauth-resource           OAuth resource
   -s, --saml-request             Base64 encoded SAML request
   -v, --saml-cert-version        Version of the SAML signing certificate
   -e, --warn-password-expiry     Warns when password will expire soon
   -h, --help                     Shows help
```

---

## Token Types and Duration

**Note:** Access token duration is not configurable via the CLI - it's determined by the OAuth server configuration for the specific client ID.

| Token Type | Duration | Auto-Refresh |
|------------|----------|--------------|
| AppleConnect Access Token | ~30 minutes | ✅ Yes (every 25 min) |
| AppleConnect Refresh Token | Hours to days | N/A |
| FloodGate Project Token | Does not expire | ❌ No |
| Static OIDC Token | Varies | ❌ No |

---

## Automatic Token Refresh

The `code_analyzer` includes **automatic token refresh** for AppleConnect tokens. This feature ensures long-running analyses complete successfully without authentication failures.

### How It Works

1. **Refresh Interval**: Tokens are refreshed every **25 minutes** (5-minute safety buffer before the ~30-minute expiry)
2. **Refresh Timing**: Token refresh happens automatically before each API request when needed
3. **No User Action Required**: The refresh is transparent - no configuration changes needed

### Token Refresh Behavior by Authentication Type

| Authentication Type | Auto-Refresh | Notes |
|---------------------|--------------|-------|
| FloodGate token (`project-credentials`) | ❌ Disabled | FloodGate tokens don't expire |
| Static credentials (`credentials` field) | ❌ Disabled | Assumed to be long-lived tokens |
| AppleConnect (no credentials in config) | ✅ Enabled | Tokens refreshed every 25 minutes |

### Log Messages to Expect

During long-running analyses, you'll see these log messages at ~25-minute intervals:

```
INFO: AppleConnect auto-refresh enabled (tokens expire in ~30 minutes)
INFO: Attempting AppleConnect token refresh...
INFO: AppleConnect token refreshed successfully (token: xxxx...xxxx)
```

### Configuration for Auto-Refresh

To use automatic token refresh, simply leave the `credentials` field empty or omit it:

```json
{
    "project_name": "MyProject",
    "api_end_point": "https://floodgate.g.apple.com/api/openai/v1/chat/completions",
    "llm_provider_type": "aws_bedrock",
    "credentials": ""
}
```

The system will:
1. Fetch an initial AppleConnect token at startup
2. Automatically refresh the token every 25 minutes
3. Update the Authorization headers before each API request

---

## Authentication Flow

### Overview

The code analyzer supports multiple LLM providers and uses a multi-step fallback mechanism for credential acquisition. For Apple internal use, it can automatically retrieve OAuth tokens using AppleConnect when no explicit API key is provided.

### Entry Point

When `code_analyzer` is run, the API key retrieval starts in the `run()` method:

```python
# hindsight/analyzers/code_analyzer.py:2090
api_key = get_api_key_from_config(config_dict)
```

### Provider-Specific Logic

The `get_api_key_from_config()` function in `hindsight/utils/config_util.py` handles different LLM providers:

| Provider | Behavior |
|----------|----------|
| `dummy` | Returns `"dummy-key"` immediately (no real auth needed) |
| `aws_bedrock` | Checks `api_key` and `credentials` config fields, then falls back to AppleConnect |
| `claude` | Checks `api_key` and `credentials` config fields only (no AppleConnect fallback) |

### AppleConnect Fallback

For the `aws_bedrock` provider, when no explicit API key is provided in the configuration, the system falls back to AppleConnect token retrieval.

The `get_api_key()` function in `hindsight/utils/api_key_util.py` implements this fallback:

```python
def get_api_key(config_api_key: Optional[str] = None) -> Optional[str]:
    # First, try the API key from config
    if config_api_key:
        return config_api_key
    
    # Fallback to Apple Connect token
    apple_token = get_apple_connect_token()
    return apple_token
```

### AppleConnect Token Retrieval

The `get_apple_connect_token()` function executes the AppleConnect CLI tool via subprocess:

```python
cmd = [
    '/usr/local/bin/appleconnect', 'getToken',
    '-C', 'hvys3fcwcteqrvw3qzkvtk86viuoqv',  # Client ID
    '--token-type=oauth',
    '--interactivity-type=none',
    '-E', 'prod',                              # Environment: production
    '-G', 'pkce',                              # Grant type: PKCE
    '-o', 'openid,dsid,accountname,profile,groups'  # OAuth scopes
]
```

#### AppleConnect Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| Client ID | `hvys3fcwcteqrvw3qzkvtk86viuoqv` | Registered application client ID |
| Token type | `oauth` | OAuth token format |
| Interactivity | `none` | Non-interactive mode (uses cached credentials) |
| Environment | `prod` | Production environment |
| Grant type | `pkce` | Proof Key for Code Exchange |
| Scopes | `openid,dsid,accountname,profile,groups` | Requested OAuth scopes |

#### Execution Details

- **Timeout**: 30 seconds
- **Token extraction**: On success (return code 0), the token is extracted from the last word of stdout
- **Error handling**: Returns `None` on timeout, missing tool, or command failure

### Flow Diagram

```
code_analyzer.run()
    │
    ▼
get_api_key_from_config(config)
    │
    ├─► If dummy provider → return "dummy-key"
    │
    ├─► If aws_bedrock → get_api_key(config.api_key or config.credentials)
    │                         │
    │                         ├─► If config has key → return it
    │                         │
    │                         └─► Else → get_apple_connect_token()
    │                                         │
    │                                         └─► Execute /usr/local/bin/appleconnect
    │                                             with OAuth PKCE flow
    │
    └─► If claude/other → Check config.api_key or config.credentials
                              │
                              └─► Return if found, else None

AWSBedrockProvider.make_request()
    │
    ├─► _refresh_headers_if_needed()  ← Auto-refresh if token age >= 25 min
    │         │
    │         └─► token_manager.get_token() → get_apple_connect_token()
    │
    └─► Make API request with fresh token
```

---

## Configuration Examples

### Using AppleConnect Auto-Refresh (Recommended for Development)

```json
{
    "project_name": "MyProject",
    "api_end_point": "https://floodgate.g.apple.com/api/openai/v1/chat/completions",
    "llm_provider_type": "aws_bedrock",
    "credentials": ""
}
```

When no `credentials` or `api_key` is provided with `aws_bedrock` provider, the system:
1. Automatically retrieves an OAuth token via AppleConnect
2. Refreshes the token every 25 minutes during long-running analyses

### Using FloodGate Project Token (Recommended for CI/CD)

```json
{
    "project_name": "MyProject",
    "api_end_point": "https://floodgate.g.apple.com/api/openai/v1/chat/completions",
    "llm_provider_type": "aws_bedrock",
    "project-credentials": "your-floodgate-project-token"
}
```

FloodGate tokens don't expire, so no auto-refresh is needed.

### Using Static OIDC Token

```json
{
    "project_name": "MyProject",
    "api_end_point": "https://floodgate.g.apple.com/api/openai/v1/chat/completions",
    "llm_provider_type": "aws_bedrock",
    "credentials": "your-oidc-token"
}
```

---

## Implementation Details

### Key Files

| File | Role |
|------|------|
| `hindsight/utils/api_key_util.py` | `AppleConnectTokenManager` class, `get_apple_connect_token()` |
| `hindsight/utils/config_util.py` | `get_api_key_from_config()`, provider-specific logic |
| `hindsight/core/llm/providers/aws_bedrock_provider.py` | `_refresh_headers_if_needed()`, auto-refresh integration |

### AppleConnectTokenManager Class

The `AppleConnectTokenManager` is a singleton class that:
- Tracks when the token was acquired
- Automatically refreshes tokens before they expire (at 25-minute intervals)
- Bypasses refresh logic for static API keys from config

```python
# hindsight/utils/api_key_util.py

# Default token refresh interval: 25 minutes (tokens expire in ~30 minutes)
DEFAULT_TOKEN_REFRESH_INTERVAL_SECONDS = 25 * 60  # 1500 seconds

class AppleConnectTokenManager:
    """
    Manages AppleConnect OAuth tokens with automatic refresh support.
    
    Usage:
        token_manager = AppleConnectTokenManager()
        token = token_manager.get_token()  # Auto-refreshes if needed
    """
```

### Key Methods

| Method | Description |
|--------|-------------|
| `get_token()` | Returns current token, refreshing if needed |
| `needs_refresh()` | Returns `True` if token age >= 25 minutes |
| `refresh_token()` | Forces a token refresh via AppleConnect |
| `get_token_age_seconds()` | Returns how old the current token is |

### AWSBedrockProvider Integration

The provider calls `_refresh_headers_if_needed()` before each API request:

```python
# hindsight/core/llm/providers/aws_bedrock_provider.py

def _refresh_headers_if_needed(self) -> None:
    """Refresh headers with AppleConnect token if using auto-refresh mode."""
    if not self._use_apple_connect_auto_refresh:
        return
    
    token_manager = get_token_manager()
    token = token_manager.get_token()  # Auto-refreshes if needed
    
    if token:
        self.headers["Authorization"] = f"Bearer {token}"
        self.headers["X-Apple-OIDC-Token"] = token
```

---

## Troubleshooting

### Prerequisites for AppleConnect

1. The AppleConnect CLI tool must be installed at `/usr/local/bin/appleconnect`
2. The user must have valid cached credentials (or be able to authenticate interactively if `--interactivity-type` is changed)
3. The user must have appropriate permissions for the requested OAuth scopes

### Error Handling

| Error | Behavior |
|-------|----------|
| AppleConnect tool not found | Logs warning, returns `None` |
| Command timeout (>30s) | Logs warning, returns `None` |
| Command failure (non-zero exit) | Logs warning with stderr, returns `None` |
| Empty token in output | Logs warning, returns `None` |
| Token refresh failure | Logs warning, continues with old token |

### Common Issues

**Q: Analysis fails after ~30 minutes with authentication errors**

A: Check that auto-refresh is enabled. Look for this log message at startup:
```
INFO: AppleConnect auto-refresh enabled (tokens expire in ~30 minutes)
```

If you see `Using legacy api_key for Authorization` without the auto-refresh message, the token won't be refreshed.

**Q: Token refresh not happening**

A: Ensure you're using `aws_bedrock` provider with empty `credentials`. If you provide a static token in `credentials`, auto-refresh is disabled.

**Q: AppleConnect command fails**

A: Try running the command manually to diagnose:
```bash
/usr/local/bin/appleconnect getToken \
    -C hvys3fcwcteqrvw3qzkvtk86viuoqv \
    --token-type=oauth \
    --interactivity-type=none \
    -E prod -G pkce \
    -o openid,dsid,accountname,profile,groups
```

### Alternative Options

If auto-refresh doesn't work for your use case:

1. **Use FloodGate project token** (for CI/CD or service accounts):
   ```json
   {
       "project-credentials": "your-floodgate-token"
   }
   ```

2. **Break analysis into smaller batches** (for very large codebases):
   ```bash
   python -m hindsight.analyzers.code_analyzer \
       --config config.json \
       --repo /path/to/repo \
       --num-functions-to-analyze 50
   ```
   The tool caches results, so subsequent runs skip already-analyzed functions.
