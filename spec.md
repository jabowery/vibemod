ModSpec – DSL for Program Modification Directives (Two‑Level Specification)
Design Philosophy: Fail fast on invalid input. Operations are not defensive—validation occurs in the test suite.

1) Syntax Layer – Lexical Grammar (EBNF)
file        := { block }

block       := command nl block_body
command     := "MMM" ws ident ws "MMM"
ident       := letter { letter | digit | "_" }

block_body  := section { sep section } nlopt
section     := { line }
sep         := nl? "@@@@@@" nl?           # lines equal to "@@@@@@" after strip()

nl          := { "\r\n" | "\n" }+
nlopt       := { "\r\n" | "\n" }
ws          := { " " | "\t" }+
line        := textline
textline    := ( "\\" "@@@@@@" ) | ( any_char_except_eol )*
```
**Notes**
- Defines only surface structure: how to split text into `MMM` blocks and `@@@@@@` sections.
- Does **not** assign meaning to sections; semantics is layered on top.
- Whitespace and line endings are permissive throughout.

---

## 2) Semantics Layer – Command Registry & Schema Resolver
Each command name corresponds to a set of **schemas** (arity and pattern rules). The resolver maps raw sections → typed arguments using these schemas.

### Core file commands
| Command | Arity | Semantics |
|----------|--------|-----------|
| `modification_description` | 1 | Description text only |
| `create_file` | 2–3 | `(path, content, [make_executable])` |
| `replace_file_contents` | 2–3 | `(path, content, [make_executable])` |
| `move_file` | 2 | `(src, dst)` — overwrites destination if exists |
| `make_directory` | 1 | `(path)` |
| `remove_file` | 1 | `(path)` — deletes file or directory recursively |
| `update_header` | 1 | `(new_header_text)` |

**Notes**:
- `make_executable`: if true, chmod a+x (Unix only; no-op on Windows)
- `header` is a file's contents prior to the first declaration of a class or def

### Code‑level commands (scope‑aware)
`dotted_target := name { "." name }`

#### declare
- **Schema A – Shorthand (2 sections)**  
  sections = `[combined, content]`
  - `combined` must match `^(.+?\.py)\.(.+)$`
  - Split at the **first** `.py` → `file_path = group1 + '.py'`, `dotted_target = group2`
  - `content` must be non‑empty.  A definition includes any decorators.
  - Action → add/replace `dotted_target` in `file_path`.

- **Schema B – Explicit (3 sections)**  
  sections = `[file_path, dotted_target, content]`
  - `content` must be non‑empty.
  - Action → add/replace `dotted_target` in `file_path`.

#### update_declaration
- **Schema – (3 sections)** → `[file_path, dotted_target, content]`
  - `content` must be non‑empty.
  - Action → add/replace only (no delete).

#### remove_declaration
- **Schema – (2 sections)** → `[file_path, dotted_target]`
  - Must have exactly two sections (no content block).
  - Action → delete all matches of `dotted_target` in `file_path`.

### Resolver algorithm (deterministic)
1. Parse block → `(cmd_name, sections[])`.
2. Lookup schemas for `cmd_name` in priority order.
   - For `declare`: [Schema A, Schema B].
3. For each schema, check arity and validators (`regex`, non‑empty, etc.).
4. Use the first schema that validates; raise error otherwise.
5. Execute operation using AST replacement rules:
   - Replace all matches of `dotted_target` in AST.
   - Classes and functions are treated identically for matching and insertion.
   - `dotted_target` matches any declaration path: `module.Class`, `module.function`, `module.Class.method`, `module.function.nested_function`, etc.
   - Preserve decorators and indentation.
   - If no match found, insert before the first top‑level declaration (class/def) at the same AST scope level.
   - If no declarations exist, append to end of file.

---

## 3) Conventions
- **Booleans**: accept `true/false/yes/no/y/n/1/0` (case‑insensitive).
- **Paths**: relative paths resolve from current working directory.
- **dotted_target**: strictly matches `name(.name)*`.
- **Files**: UTF‑8 encoded; line endings preserved as‑is.
- **Error policy**: operations fail fast on invalid input, AST errors, or missing files.

---

## 4) Attribute‑Grammar Perspective
```
Block(name, sections[]) → Cmd(name, Args)
Args := SchemaResolver(name, sections)
SchemaResolver checks:
  is_shorthand(s0) := regex("^(.+?\\.py)\\.(.+)$")
  nonempty(s) := len(s.strip()) > 0
  valid_target(t) := matches name(.name)*
```
This makes the syntax→semantics mapping explicit for testing and CI.

---

## 5) Example (Shorthand Form)
```
MMM declare MMM
src/foo/bar.py.baz_function
@@@@@@
def baz_function():
    return 42
→ Parsed as (file_path="src/foo/bar.py", dotted_target="baz_function", content = def block)

6) Validator Checklist (for CI)

 Known command name.
 Valid schema detected.
 Section count matches schema.
 Non‑empty content for declare/update.
 No content for remove.
 Valid dotted_target.
 Line endings/encoding preserved.

### 6) git repository and exceptions
- Before modifications are applied, any uncommitted files are commited to the git repository with a comment 'preparing execute automated modifications' and then try: the apply_modspec after which commit the changes (including any file operations such as add, rm, mv) with a comment determined by the by modification_description. The except should reset to the prior commit.
- This means all exceptions internal to the processing must re-raise the exception so that the reset can be done.
- If there is no git repository, the modification system immediately exist without doing any modifications.
- Previously untracked files should not be added to the repo unless they were among the file paths in the modification set.
