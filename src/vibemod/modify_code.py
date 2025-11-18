import ast
import os
import re
import shutil
import sys
import subprocess
import textwrap

# ─────────────────────────── git helpers ───────────────────────────

def run_git(cmd, check=True):
    result = subprocess.run(['git'] + cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f'Git command failed: {result.stdout + result.stderr}')
    return result.stdout.strip()

# ─────────────────────────── MMM constants ───────────────────────────

# Canonical header: "MMM <command> MMM"
HEADER_RE_STRICT = re.compile(r'^\s*MMM\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+MMM\s*$')

# Section separator and escape sequence in the *spec file*.
# In the file, the escape is literally "\@@@@@@".
SEP = '@@@@@@'
ESCAPE = r'\@@@@@@'  # one backslash + six @ in the spec file

# Lax header for adapter (tolerates trailing junk/whitespace)
HEADER_RE_LAX = re.compile(r'^\s*MMM\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+MMM\b.*$')


# ─────────────────────────── adapter: normalize LLM quirks ───────────────────────────

def adapter_normalize(file_content: str) -> str:
    """
    Normalize common LLM formatting quirks into a canonical MMM form
    *without* changing the semantics of the language.
    
    This layer is where we adapt messy LLM output to the strict grammar.
    """
    lines = file_content.splitlines(keepends=True)
    out_lines: list[str] = []

    for line in lines:
        # Normalize MMM headers with trailing spaces/junk:
        # "   MMM   create_file   MMM   "  -> "MMM create_file MMM\n"
        m = HEADER_RE_LAX.match(line)
        if m:
            cmd = m.group(1)
            out_lines.append(f"MMM {cmd} MMM\n")
            continue

        stripped = line.strip()

        # Normalize section separator lines with stray spaces:
        # "  @@@@@@   " -> "@@@@@@\n"
        if stripped == SEP:
            out_lines.append(SEP + "\n")
            continue

        # Normalize spaced escape sequences:
        # "\ @@@@@@"  or  "\   @@@@@@"  -> "\@@@@@@"
        # LLMs often inject spaces after the backslash.
        # We only touch backslash + spaces + @@@@@@, leaving other content alone.
        line = re.sub(r'\\\s*@@@@@@', ESCAPE, line)

        out_lines.append(line)

    return ''.join(out_lines)


# ─────────────────────────── strict parser ───────────────────────────

def strict_parse(file_content: str) -> list[tuple[str, list[str]]]:
    r"""
    Parse ModSpec content into blocks of (command, sections) using a
    strict, deterministic grammar. Assumes content has already been
    normalized by adapter_normalize.
    
    Grammar (informal):
    
      File  ::= { Block }
      Block ::= Header Body
      Header ::= "MMM" <command> "MMM"
      Body   ::= { Line }
      Sections in Body are separated by lines whose stripped() == "@@@@@@".
      Literal "@@@@@@" inside a section is written as "\@@@@@@" and is
      decoded here to "@@@@@@" in the section content.
    """
    lines = file_content.splitlines(keepends=True)
    blocks: list[tuple[str, list[str]]] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        m = HEADER_RE_STRICT.match(line)
        if not m:
            i += 1
            continue

        cmd = m.group(1)
        i += 1

        # Collect body lines until next header or EOF
        body_lines: list[str] = []
        while i < len(lines):
            next_line = lines[i]
            if HEADER_RE_STRICT.match(next_line):
                break
            body_lines.append(next_line)
            i += 1

        # Split body into sections by SEP lines, handle ESCAPE in content
        sections: list[str] = []
        current_section: list[str] = []

        for bl in body_lines:
            stripped = bl.strip()
            if stripped == SEP:
                sections.append(''.join(current_section))
                current_section = []
            else:
                # Replace literal escape with actual separator inside content
                # Input: "\@@@@@@" -> "@@@@@@"
                if ESCAPE in bl:
                    bl = bl.replace(ESCAPE, SEP)
                current_section.append(bl)

        if current_section:
            sections.append(''.join(current_section))

        blocks.append((cmd, sections))

    return blocks


def parse(file_content: str) -> list[tuple[str, list[str]]]:
    """
    Public parse API: adapter + strict parser.
    
    This is the function other tooling should call.
    """
    normalized = adapter_normalize(file_content)
    return strict_parse(normalized)


# ─────────────────────────── helpers for resolve/execute ───────────────────────────

def is_valid_dotted(t: str) -> bool:
    name_re = r'[a-zA-Z_][a-zA-Z0-9_]*'
    pattern = f'^{name_re}(\\.{name_re})*$'
    return bool(re.match(pattern, t))


def parse_bool(s: str) -> bool:
    s = s.strip().lower()
    if s in ['true', 'yes', 'y', '1']:
        return True
    if s in ['false', 'no', 'n', '0']:
        return False
    raise ValueError(f'Invalid boolean: {s!r}')


# ─────────────────────────── resolve: structural, no heuristics ───────────────────────────

def resolve(cmd: str, sections: list[str]):
    """
    Convert (cmd, raw_sections) into a structured argument list for execute().
    
    This function is *purely structural*: it does not try to "fix up"
    arities by trimming or guessing. If the arity is wrong, it raises.
    """
    arity = len(sections)

    if cmd == 'modification_description':
        if arity != 1:
            raise ValueError(f'{cmd} requires arity 1, got {arity}')
        return [sections[0]]

    elif cmd in ['create_file', 'replace_file_contents']:
        # sections: [path, content] or [path, content, make_exec_flag]
        if arity not in (2, 3):
            raise ValueError(f'{cmd} requires arity 2 or 3, got {arity}')
        path = sections[0].strip()
        content = sections[1]
        make_exec = False
        if arity == 3:
            flag_str = sections[2].strip()
            if flag_str == '':
                raise ValueError(f'{cmd} third section (make_exec) must be a non-empty boolean')
            make_exec = parse_bool(flag_str)
        return [path, content, make_exec]

    elif cmd == 'move_file':
        if arity != 2:
            raise ValueError(f'{cmd} requires arity 2, got {arity}')
        return [sections[0].strip(), sections[1].strip()]

    elif cmd == 'make_directory':
        if arity != 1:
            raise ValueError(f'{cmd} requires arity 1, got {arity}')
        return [sections[0].strip()]

    elif cmd == 'remove_file':
        if arity != 1:
            raise ValueError(f'{cmd} requires arity 1, got {arity}')
        return [sections[0].strip()]

    elif cmd == 'update_header':
        if arity != 2:
            raise ValueError(f'{cmd} requires arity 2, got {arity}')
        return [sections[0].strip(), sections[1]]

    elif cmd == 'declare':
        if arity == 2:
            # Shorthand: "<file.py>.<dotted_target>" + content
            combined = sections[0].strip()
            m = re.match(r'^(.+?\.py)\.(.+)$', combined)
            if not m:
                raise ValueError('Invalid shorthand for declare: expected "<file.py>.<dotted_target>"')
            file_path = m.group(1)
            dotted_target = m.group(2)
            content = sections[1]
            if not content.strip():
                raise ValueError('Content must be non-empty')
            if not is_valid_dotted(dotted_target):
                raise ValueError('Invalid dotted_target')
            return [file_path, dotted_target, content]
        elif arity == 3:
            file_path = sections[0].strip()
            dotted_target = sections[1].strip()
            content = sections[2]
            if not content.strip():
                raise ValueError('Content must be non-empty')
            if not is_valid_dotted(dotted_target):
                raise ValueError('Invalid dotted_target')
            return [file_path, dotted_target, content]
        else:
            raise ValueError('declare requires arity 2 or 3')

    elif cmd == 'update_declaration':
        if arity != 3:
            raise ValueError('update_declaration requires arity 3')
        file_path = sections[0].strip()
        dotted_target = sections[1].strip()
        content = sections[2]
        if not content.strip():
            raise ValueError('Content must be non-empty')
        if not is_valid_dotted(dotted_target):
            raise ValueError('Invalid dotted_target')
        return [file_path, dotted_target, content]

    elif cmd == 'remove_declaration':
        if arity != 2:
            raise ValueError('remove_declaration requires arity 2')
        file_path = sections[0].strip()
        dotted_target = sections[1].strip()
        if not is_valid_dotted(dotted_target):
            raise ValueError('Invalid dotted_target')
        return [file_path, dotted_target]

    else:
        raise ValueError(f'Unknown command: {cmd}')


# ─────────────────────────── AST helpers for declarations ───────────────────────────

def get_scope(tree: ast.AST, parts: list[str]):
    current = tree
    for part in parts[:-1]:
        found = None
        for node in getattr(current, "body", []):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == part:
                found = node
                break
        if found is None:
            return None
        current = found
    return getattr(current, "body", None)


def modify_declaration(file_path: str, dotted_target: str, content: str | None, remove: bool):
    if not os.path.exists(file_path):
        if remove:
            return
        raise FileNotFoundError(f'File not found: {file_path}')

    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ValueError(f'AST parse error in {file_path}') from e

    parts = dotted_target.split('.')
    if not parts:
        raise ValueError('Invalid dotted_target')

    scope = get_scope(tree, parts)
    if scope is None:
        if remove:
            return
        raise ValueError(f'Parent scope not found for {dotted_target} in {file_path}')

    name = parts[-1]
    new_node = None

    if not remove:
        content = textwrap.dedent(content)
        try:
            content_module = ast.parse(content)
        except SyntaxError as e:
            raise ValueError('Invalid content syntax') from e

        body = content_module.body
        imports_to_add = []
        while body and isinstance(body[0], (ast.Import, ast.ImportFrom)):
            imports_to_add.append(body.pop(0))

        if len(body) != 1:
            raise ValueError('Content must be a single declaration, optionally preceded by import statements')

        decl = body[0]
        if not isinstance(decl, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            raise ValueError('Content must be a class or function definition')
        if decl.name != name:
            raise ValueError(f'Name mismatch: expected {name}, got {decl.name}')
        new_node = decl

        if imports_to_add:
            def get_import_key(node: ast.AST) -> tuple | None:
                if isinstance(node, ast.Import):
                    return ('import', tuple(sorted(((alias.name, alias.asname or '') for alias in node.names))))
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    return ('from', module, tuple(sorted(((alias.name, alias.asname or '') for alias in node.names))))
                return None

            existing_keys = {
                get_import_key(node)
                for node in tree.body
                if get_import_key(node) is not None
            }

            insert_idx = 0
            if (tree.body and isinstance(tree.body[0], ast.Expr)
                    and isinstance(tree.body[0].value, ast.Constant)
                    and isinstance(tree.body[0].value.value, str)):
                insert_idx = 1

            while insert_idx < len(tree.body) and get_import_key(tree.body[insert_idx]) is not None:
                insert_idx += 1

            for imp in imports_to_add:
                key = get_import_key(imp)
                if key and key not in existing_keys:
                    tree.body.insert(insert_idx, imp)
                    insert_idx += 1
                    existing_keys.add(key)

    # Remove existing declarations with this name
    existing_indices = [
        i for i, node in enumerate(scope)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    for i in sorted(existing_indices, reverse=True):
        del scope[i]

    if not remove and new_node is not None:
        pos = len(scope)
        if existing_indices:
            pos = min(existing_indices)
        else:
            for i, node in enumerate(scope):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    pos = i
                    break
        scope.insert(pos, new_node)

    new_source = ast.unparse(tree)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_source + '\n')


# ─────────────────────────── command executor ───────────────────────────

def execute(cmd: str, args):
    if cmd == 'modification_description':
        return

    elif cmd in ['create_file', 'replace_file_contents']:
        path, content, make_exec = args
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        if make_exec and not sys.platform.startswith('win'):
            # 0o755
            os.chmod(path, 0o755)

    elif cmd == 'move_file':
        src, dst = args
        shutil.move(src, dst)

    elif cmd == 'make_directory':
        path = args[0]
        os.makedirs(path, exist_ok=True)

    elif cmd == 'remove_file':
        path = args[0]
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.isfile(path):
            os.remove(path)
        else:
            raise FileNotFoundError(f'No such file or directory: {path}')

    elif cmd == 'update_header':
        file_path, new_header = args
        if not os.path.exists(file_path):
            raise FileNotFoundError(f'File not found: {file_path}')
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            raise ValueError(f'AST parse error in {file_path}') from e
        decl_nodes = [
            node for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if not decl_nodes:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_header + '\n')
            return
        first_decl_lineno = min(node.lineno for node in decl_nodes)
        lines = source.splitlines(keepends=True)
        decl_and_after = lines[first_decl_lineno - 1:]
        new_header_lines = new_header.splitlines(keepends=True)
        if new_header and not new_header.endswith(('\n', '\r\n')):
            new_header_lines[-1] += '\n'
        new_source = ''.join(new_header_lines + decl_and_after)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_source)

    elif cmd in ['declare', 'update_declaration']:
        file_path, dotted_target, content = args
        modify_declaration(file_path, dotted_target, content, remove=False)

    elif cmd == 'remove_declaration':
        file_path, dotted_target = args
        modify_declaration(file_path, dotted_target, None, remove=True)


# ─────────────────────────── top-level apply_modspec ───────────────────────────

def apply_modspec(spec_file: str):
    with open(spec_file, 'r', encoding='utf-8') as f:
        content = f.read()

    blocks = parse(content)

    descriptions: list[str] = []
    touched: set[str] = set()

    # First pass: compute description and touched paths
    for cmd, sections in blocks:
        args = resolve(cmd, sections)
        if cmd == 'modification_description':
            descriptions.append(args[0])
        elif cmd in ['create_file', 'replace_file_contents',
                     'make_directory', 'remove_file',
                     'update_header', 'declare', 'update_declaration',
                     'remove_declaration']:
            touched.add(args[0])
        elif cmd == 'move_file':
            touched.add(args[0])
            touched.add(args[1])

    desc = '\n'.join(descriptions).strip() or 'Automated modifications'

    prior_commit = run_git(['rev-parse', 'HEAD'])
    status = run_git(['status', '--porcelain'])
    has_tracked_changes = any(
        (not line.startswith('??') for line in status.splitlines() if line)
    )

    if has_tracked_changes:
        run_git(['add', '-u'])
        run_git(['commit', '-m', 'preparing to execute automated modifications'])

    try:
        # Second pass: actually execute
        for cmd, sections in blocks:
            if cmd == 'modification_description':
                continue
            args = resolve(cmd, sections)
            execute(cmd, args)

        for path in touched:
            run_git(['add', path], check=False)

        try:
            run_git(['commit', '-m', desc])
        except RuntimeError as e:
            if 'nothing to commit' not in str(e):
                raise
    except Exception:
        run_git(['reset', '--hard', prior_commit])
        raise


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('Usage: python -m modspec <spec_file>')
        sys.exit(1)
    apply_modspec(sys.argv[1])
