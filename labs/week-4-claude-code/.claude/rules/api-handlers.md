---
globs: ["src/api/**/*.ts"]
---

# API Handler Conventions

These rules apply to all API handler files under `src/api/`. They are loaded automatically when Claude Code works on any `.ts` file in that directory tree.

## Async / Await

- All handler functions must be `async` and return `Promise<Response>` (or the framework's typed equivalent).
- Never use `.then()` / `.catch()` chains inside handlers — use `await` with `try/catch`.

## Error Handling

Every handler must contain a top-level `try/catch` block. Errors must be returned as typed error responses, not thrown to the caller:

```typescript
try {
  // handler logic
} catch (error) {
  const message = error instanceof Error ? error.message : "Unknown error";
  return Response.json({ error: message }, { status: 500 });
}
```

- Do not let unhandled promise rejections propagate out of a handler.
- Do not swallow errors silently (`catch (_) {}`).

## Response Shape

Every handler response must include a `status` field:
- `"success"` — request completed normally
- `"validation_error"` — caller sent invalid input
- `"not_found"` — resource does not exist
- `"internal_error"` — unexpected server-side failure

## Input Validation

- Validate all request inputs at the top of the handler before any business logic.
- Return `status: "validation_error"` with a descriptive `message` if validation fails.
- Do not pass unvalidated input to downstream services or database queries.

## What Claude Code must NOT do here

- Do not mix `.then()` chains with `async/await` in the same function.
- Do not add a `catch` block that only logs and re-throws — wrap into a typed error response instead.
- Do not return different response shapes from different code paths in the same handler.
