---
globs: ["**/*.test.ts", "**/*.spec.ts", "**/*.test.tsx", "**/*.spec.tsx"]
---

# Test File Conventions

These rules apply to ALL test files anywhere in the codebase — identified by the `.test.ts`, `.spec.ts`, `.test.tsx`, or `.spec.tsx` suffix. They apply regardless of which directory the file lives in.

This is the key reason this rule is in `.claude/rules/` rather than a `CLAUDE.md` file: a `CLAUDE.md` in `src/components/` would apply to all files in that directory, but cannot reach test files in `src/api/`. This glob-based rule reaches both.

## Mock Isolation

- Every external dependency (network calls, database, file system, third-party SDKs) must be mocked in tests.
- Mocks must be declared at the top of the file, before any `describe` blocks.
- Use `jest.mock("module-path")` for module-level mocks. Do not use `jest.spyOn` as a substitute for a module mock.
- Each test must reset all mocks: use `beforeEach(() => { jest.resetAllMocks(); })`.

## Test Structure

- One `describe` block per file, named after the module under test.
- Test names must be complete sentences: `"returns access_failure when the account does not exist"`, not `"access failure"`.
- Arrange / Act / Assert order within every test. Do not mix setup and assertion.

## Assertions

- Use specific matchers: `toEqual`, `toStrictEqual`, `toHaveBeenCalledWith` — not just `toBeTruthy` or `toBeDefined` unless the value's shape genuinely does not matter.
- Every test must have at least one assertion. A test with no `expect()` must fail.

## Coverage

- The happy path and at least one error path must be tested for every public function.
- Do not test implementation details (private functions, internal state) — test the observable output.

## What Claude Code must NOT do here

- Do not write integration tests (tests that hit real network endpoints or real databases) in files covered by this rule. Integration tests live in `tests/integration/` and are not covered by this glob.
- Do not remove `beforeEach(() => { jest.resetAllMocks(); })` — shared mock state between tests causes intermittent failures.
- Do not use `// @ts-ignore` to suppress TypeScript errors in mock setup — fix the mock type instead.
