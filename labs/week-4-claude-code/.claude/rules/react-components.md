---
globs: ["src/components/**/*.tsx"]
---

# React Component Conventions

These rules apply to all React components under `src/components/`. They are loaded automatically when Claude Code works on any `.tsx` file in that directory tree.

## Style

- Use **functional components only**. Do not write class components.
- Define props with an explicit TypeScript interface named `<ComponentName>Props`.
- Destructure props in the function signature: `function Button({ label, onClick }: ButtonProps)`.

## Hooks

- Use React hooks (`useState`, `useEffect`, `useCallback`, `useMemo`) rather than lifecycle-equivalent patterns.
- `useEffect` must always specify a dependency array. An empty array `[]` is acceptable only when the effect truly runs once on mount.
- Extract complex hook logic into a custom hook in `src/hooks/` rather than embedding it in the component.

## Exports

- Use named exports, not default exports: `export function Button(...)`.
- One component per file. The filename must match the component name exactly.

## Styling

- Use CSS modules (`Button.module.css`) for component-scoped styles.
- Do not use inline `style={{...}}` objects except for values that are dynamically computed at render time.

## What Claude Code must NOT do here

- Do not convert a functional component to a class component.
- Do not remove the props interface — even if props are not yet used.
- Do not add default exports to files in this directory.
