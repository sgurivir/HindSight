## Output Requirements

**IMPORTANT**: Respond ONLY with valid JSON. No additional text, explanations, or markdown formatting.

Return an array of issue objects following this exact MANDATORY JSON OUTPUT schema:

```json
{output_schema}
```

**RESPONSE RULES**:
- Return empty array `[]` if no issues found
- All fields are required and must be strings
- Use double quotes for all strings
- NO explanatory text, reasoning, or markdown - ONLY JSON
- Multiple issues must be separate objects in the array
- Keep descriptions concise but informative
- Solutions should be actionable and specific
- **MUST RULE - Line Number Format**: The "lines" field should contain ONLY line numbers or line ranges (e.g., "45" or "45-48"), NEVER actual lines of code
- **Line numbers must match the numbered content provided - do not guess or approximate**

**CRITICAL OUTPUT REQUIREMENT**:
YOU MUST RESPOND WITH ONLY VALID JSON - NO OTHER TEXT ALLOWED

**ABSOLUTE REQUIREMENT**: Your response must start with `[` and end with `]`. No explanatory text, no reasoning, no markdown, no code blocks, no analysis description - ONLY the JSON array.

**FORBIDDEN**: Any text before or after the JSON array will cause system failure.