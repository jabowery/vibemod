import ast
import os
import re
import shutil
import sys
import subprocess
import textwrap
from dataclasses import dataclass
from typing import List, Tuple, Any


def _update_header_rust(file_path: str, source: str, new_header: str):
    """Rust-specific header update using tree-sitter."""
    from .handlers import get_handler
    handler = get_handler(file_path)
    header_end = handler.find_header_end(source)
    new_header_clean = new_header.strip() + '\n\n'
    new_source = new_header_clean + source[header_end:]
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_source)

def _update_header_python(file_path: str, source: str, new_header: str):
    """Python-specific header update."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ValueError(f'AST parse error in {file_path}') from e
    decl_nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
    if not decl_nodes:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_header + '\n')
        return
    first_decl_lineno = min((node.lineno for node in decl_nodes))
    lines = source.splitlines(keepends=True)
    decl_and_after = lines[first_decl_lineno - 1:]
    new_header_lines = new_header.splitlines(keepends=True)
    if new_header and (not new_header.endswith(('\n', '\r\n'))):
        new_header_lines[-1] += '\n'
    new_source = ''.join(new_header_lines + decl_and_after)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_source)

def _execute_update_header(file_path: str, new_header: str):
    """Execute update_header command with language-aware dispatch."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f'File not found: {file_path}')
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()
    ext = os.path.splitext(file_path)[1]
    if ext == '.py':
        _update_header_python(file_path, source, new_header)
    elif ext == '.rs':
        _update_header_rust(file_path, source, new_header)
    else:
        from .handlers import get_handler
        handler = get_handler(file_path)
        header_end = handler.find_header_end(source)
        new_header_clean = new_header.strip() + '\n\n'
        new_source = new_header_clean + source[header_end:]
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_source)

def _modify_declaration_rust(file_path: str, source: str, dotted_target: str, content: str | None, remove: bool):
    """Rust-specific declaration modification using tree-sitter."""
    from .handlers import get_handler
    handler = get_handler(file_path)
    loc = handler.find_declaration(source, dotted_target)
    if remove:
        if loc is None:
            return
        start, end = loc
        new_source = source[:start].rstrip() + '\n\n' + source[end:].lstrip()
        import re
        new_source = re.sub('\\n{3,}', '\n\n', new_source)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_source)
    else:
        if content is None:
            raise ValueError('Content required for non-remove declaration operation')
        content = textwrap.dedent(content).strip()
        if loc:
            start, end = loc
            new_source = source[:start] + content + source[end:]
        else:
            new_source = source.rstrip() + '\n\n' + content + '\n'
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_source)

def _modify_declaration_python(file_path: str, source: str, dotted_target: str, content: str | None, remove: bool):
    """Python-specific declaration modification using AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ValueError(f'AST parse error in {file_path}') from e
    original_lines = source.splitlines(keepends=True)
    original_decl_nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
    header_end_line = 0
    if original_decl_nodes:
        start_lines = []
        for node in original_decl_nodes:
            if hasattr(node, 'decorator_list') and node.decorator_list:
                start_lines.append(node.decorator_list[0].lineno)
            else:
                start_lines.append(node.lineno)
        first_decl_lineno = min(start_lines)
        header_end_line = first_decl_lineno - 1
    parts = dotted_target.split('.')
    if not parts:
        raise ValueError('Invalid dotted_target')
    scope = get_scope(tree, parts[:-1]) if len(parts) > 1 else tree
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

            def get_import_key(node: ast.AST) -> Tuple | None:
                if isinstance(node, ast.Import):
                    return ('import', tuple(sorted(((alias.name, alias.asname or '') for alias in node.names))))
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    return ('from', module, tuple(sorted(((alias.name, alias.asname or '') for alias in node.names))))
                return None
            existing_keys = {get_import_key(node) for node in tree.body if get_import_key(node) is not None}
            insert_idx = 0
            if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str):
                insert_idx = 1
            while insert_idx < len(tree.body) and get_import_key(tree.body[insert_idx]) is not None:
                insert_idx += 1
            for imp in imports_to_add:
                key = get_import_key(imp)
                if key and key not in existing_keys:
                    tree.body.insert(insert_idx, imp)
                    insert_idx += 1
                    existing_keys.add(key)
    body_to_modify = scope.body if hasattr(scope, 'body') else scope
    existing_indices = [i for i, node in enumerate(body_to_modify) if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    for i in sorted(existing_indices, reverse=True):
        del body_to_modify[i]
    if not remove and new_node is not None:
        pos = len(body_to_modify)
        if existing_indices:
            pos = min(existing_indices)
        else:
            for i, node in enumerate(body_to_modify):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    pos = i
                    break
        body_to_modify.insert(pos, new_node)
    if header_end_line > 0:
        header = ''.join(original_lines[:header_end_line])
        new_source = ast.unparse(tree)
        new_lines = new_source.splitlines(keepends=True)
        new_tree = ast.parse(new_source)
        new_decl_nodes = [node for node in new_tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
        if new_decl_nodes:
            new_start_lines = []
            for node in new_decl_nodes:
                if hasattr(node, 'decorator_list') and node.decorator_list:
                    new_start_lines.append(node.decorator_list[0].lineno)
                else:
                    new_start_lines.append(node.lineno)
            new_first_decl_lineno = min(new_start_lines)
            decls_part = ''.join(new_lines[new_first_decl_lineno - 1:])
            final_source = header + decls_part
        else:
            final_source = header
    else:
        final_source = ast.unparse(tree) + '\n'
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(final_source)

def run_git(cmd, check=True):
    result = subprocess.run(['git'] + cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f'Git command failed: {result.stdout + result.stderr}')
    return result.stdout.strip()
HEADER_RE_PERMISSIVE = re.compile('^\\s*MMM\\s+([a-zA-Z_][a-zA-Z0-9_]*)\\s+MMM\\b.*$')
SEP = '@@@@@@'
ESCAPE = '\\@@@@@@'

@dataclass
class CommandBlock:
    """Represents a command with its raw arguments (sections)."""
    command: str
    arguments: List[str]

def normalize_llm_quirks(file_content: str) -> str:
    """
    Normalize common LLM formatting quirks without changing semantics.
    - Cleans up MMM headers
    - Normalizes separator lines
    - Handles escaped separators
    """
    lines = file_content.splitlines(keepends=True)
    out_lines: List[str] = []
    for line in lines:
        m = HEADER_RE_PERMISSIVE.match(line)
        if m:
            cmd = m.group(1)
            out_lines.append(f'MMM {cmd} MMM\n')
            continue
        stripped = line.strip()
        if stripped == SEP:
            out_lines.append(SEP + '\n')
            continue
        line = re.sub('\\\\\\s*@@@@@@', ESCAPE, line)
        out_lines.append(line)
    return ''.join(out_lines)

def extract_command_blocks(file_content: str) -> List[CommandBlock]:
    """
    Extract command blocks using permissive grammar.
    
    Grammar:
        File := [LeadingDescription]? CommandBlock*
        LeadingDescription := <text_until_first_command>  # Auto-wrapped as modification_description
        CommandBlock := Command Argument*
        Command := "MMM" <identifier> "MMM"
        Argument := <text_until_separator_or_next_command>
        
    Returns list of CommandBlock objects with raw arguments.
    """
    content = normalize_llm_quirks(file_content)
    lines = content.splitlines(keepends=True)
    blocks: List[CommandBlock] = []
    header_re = re.compile('^\\s*MMM\\s+([a-zA-Z_][a-zA-Z0-9_]*)\\s+MMM\\s*$')
    i = 0
    leading_lines: List[str] = []
    while i < len(lines):
        line = lines[i]
        if header_re.match(line):
            break
        leading_lines.append(line)
        i += 1
    if leading_lines:
        leading_text = ''.join(leading_lines)
        blocks.append(CommandBlock(command='modification_description', arguments=[leading_text]))
    while i < len(lines):
        line = lines[i]
        m = header_re.match(line)
        if not m:
            i += 1
            continue
        command = m.group(1)
        i += 1
        body_lines: List[str] = []
        while i < len(lines):
            next_line = lines[i]
            if header_re.match(next_line):
                break
            body_lines.append(next_line)
            i += 1
        arguments: List[str] = []
        current_section: List[str] = []
        for bl in body_lines:
            stripped = bl.strip()
            if stripped == SEP:
                arguments.append(''.join(current_section))
                current_section = []
            else:
                if ESCAPE in bl:
                    bl = bl.replace(ESCAPE, SEP)
                current_section.append(bl)
        if current_section:
            arguments.append(''.join(current_section))
        blocks.append(CommandBlock(command=command, arguments=arguments))
    return blocks

def parse_bool(s: str) -> bool:
    """Parse boolean from string."""
    s = s.strip().lower()
    if s in ['true', 'yes', 'y', '1', '']:
        return True
    if s in ['false', 'no', 'n', '0']:
        return False
    raise ValueError(f'Invalid boolean: {s!r}')

def is_decl(node):
    return isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))

def canonicalize_command(block: CommandBlock) -> List[Tuple[str, List[Any]]]:
    """
    Transform a CommandBlock into canonical form(s) (command, modargs).
    
    This is the "permissive grammar" that accepts various arities and
    transforms them into the canonical form expected by execute().
    
    For update_header, may return multiple commands: update_header + declare(s).
    
    Returns: List of (command_name, canonical_modargs)
    """
    cmd = block.command
    sections = block.arguments
    arity = len(sections)
    commands: List[Tuple[str, List[Any]]] = []
    if cmd == 'modification_description':
        if arity != 1:
            raise ValueError(f'{cmd} requires exactly 1 argument but got {arity}')
        return [(cmd, [sections[0]])]
    quirk_commands = ['create_file', 'replace_file_contents', 'update_file', 'replace_file', 'replace_file_contents', 'move_file', 'make_directory', 'remove_file', 'declare', 'update_declaration', 'remove_declaration']
    if cmd in quirk_commands:
        section0list = sections[0].split()
        if len(section0list) != 1:
            section0list.extend(sections[1:])
            sections = section0list
            arity = len(sections)
        if sections and sections[-1].strip() == '':
            sections = sections[:-1]
            arity -= 1
    if cmd in ['create_file', 'replace_file_contents', 'update_file', 'replace_file', 'replace_file_contents']:
        if arity < 2 or arity > 3:
            raise ValueError(f'{cmd} requires 2 or 3 arguments but got {arity}')
        path = sections[0].strip()
        content = sections[1]
        make_exec = False
        if arity == 3:
            flag_str = sections[2].strip()
            make_exec = parse_bool(flag_str)
        return [('create_file', [path, content, make_exec])]
    elif cmd == 'move_file':
        if arity != 2:
            raise ValueError(f'{cmd} requires exactly 2 arguments but got {arity}')
        return [(cmd, [sections[0].strip(), sections[1].strip()])]
    elif cmd == 'make_directory':
        if arity != 1:
            raise ValueError(f'{cmd} requires exactly 1 argument but got {arity}')
        return [(cmd, [sections[0].strip()])]
    elif cmd == 'remove_file':
        if arity < 1 or arity > 2:
            raise ValueError(f'{cmd} requires 1 or 2 arguments but got {arity}')
        path = sections[0].strip()
        recursive = False
        if arity == 2:
            recursive = parse_bool(sections[1])
        return [(cmd, [path, recursive])]
    elif cmd == 'update_header':
        if arity != 2:
            raise ValueError(f'{cmd} requires exactly 2 arguments but got {arity}')
        path = sections[0].strip()
        content = textwrap.dedent(sections[1])
        lines = content.splitlines(keepends=True)
        subcommands = []
        try:
            tree = ast.parse(content)
            decl_nodes = [n for n in tree.body if is_decl(n)]
            if decl_nodes:
                decl_nodes.sort(key=lambda n: n.lineno)
                first_decl = decl_nodes[0]
                first_line = first_decl.lineno - 1
                header_str = ''.join(lines[:first_line])
                if header_str.strip():
                    subcommands.append(('update_header', [path, header_str]))
                for decl in decl_nodes:
                    start_line = decl.lineno - 1
                    end_line = decl.end_lineno
                    decl_str = ''.join(lines[start_line:end_line])
                    target = decl.name
                    subcommands.append(('declare', [path, target, decl_str]))
            else:
                subcommands.append(('update_header', [path, content]))
        except SyntaxError:
            subcommands.append(('update_header', [path, content]))
        return subcommands
    elif cmd in ['declare', 'update_declaration']:
        if arity != 3:
            raise ValueError(f'{cmd} requires exactly 3 arguments but got {arity}')
        return [('declare', [sections[0].strip(), sections[1].strip(), sections[2]])]
    elif cmd == 'remove_declaration':
        if arity != 2:
            raise ValueError(f'{cmd} requires exactly 2 arguments but got {arity}')
        return [(cmd, [sections[0].strip(), sections[1].strip()])]
    else:
        raise ValueError(f'Unknown command: {cmd}')

def get_scope(tree: ast.AST, parts: List[str]):
    """
    Traverse the AST tree to find the scope of a nested dotted target.
    """
    scopes = [tree]
    for part in parts:
        found = None
        for node in ast.iter_child_nodes(scopes[-1]):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == part:
                    found = node
                    break
        if found is None:
            raise ValueError(f"Could not find part '{part}' in scope")
        scopes.append(found)
    return scopes[-1]

def modify_declaration(file_path: str, dotted_target: str, content: str | None, remove: bool):
    """
    Modify a declaration in a source file using language-specific handlers.

    Supports Python (.py) and Rust (.rs) files via the handler registry.
    """
    from .handlers import get_handler
    if not os.path.exists(file_path):
        if remove:
            return
        raise FileNotFoundError(f'File not found: {file_path}')
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()
    ext = os.path.splitext(file_path)[1]
    if ext == '.py':
        _modify_declaration_python(file_path, source, dotted_target, content, remove)
    elif ext == '.rs':
        _modify_declaration_rust(file_path, source, dotted_target, content, remove)
    else:
        handler = get_handler(file_path)
        raise ValueError(f'Declaration modification not yet implemented for {ext}')

def execute_canonical(cmd: str, modargs: List[Any]):
    """
    Execute a command in canonical form.

    All commands must be in their canonical form at this point:
    - modification_description(description)
    - create_file(path, content, make_exec)
    - move_file(src, dst)
    - make_directory(path)
    - remove_file(path, recursive)
    - update_header(file_path, new_code)
    - declare(file_path, target_path, new_code)
    - remove_declaration(file_path, target_path)
    """
    print(cmd)
    if cmd == 'modification_description':
        return
    elif cmd == 'create_file':
        path, content, make_exec = modargs
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        if make_exec and (not sys.platform.startswith('win')):
            os.chmod(path, 493)
    elif cmd == 'move_file':
        src, dst = modargs
        shutil.move(src, dst)
    elif cmd == 'make_directory':
        path = modargs[0]
        os.makedirs(path, exist_ok=True)
    elif cmd == 'remove_file':
        path, recursive = modargs
        if os.path.isdir(path):
            if recursive:
                shutil.rmtree(path)
            else:
                os.rmdir(path)
        elif os.path.isfile(path):
            os.remove(path)
        else:
            raise FileNotFoundError(f'No such file or directory: {path}')
    elif cmd == 'update_header':
        file_path, new_header = modargs
        _execute_update_header(file_path, new_header)
    elif cmd == 'declare':
        file_path, dotted_target, content = modargs
        modify_declaration(file_path, dotted_target, content, remove=False)
    elif cmd == 'remove_declaration':
        file_path, dotted_target = modargs
        modify_declaration(file_path, dotted_target, None, remove=True)
    else:
        raise ValueError(f'Unknown command: {cmd}')

def get_touched_files(cmd: str, modargs: List[Any]) -> List[str]:
    """Return list of files touched by this command."""
    if cmd in ['create_file', 'make_directory', 'remove_file', 'update_header', 'declare', 'remove_declaration']:
        return [modargs[0]]
    elif cmd == 'move_file':
        return [modargs[0], modargs[1]]
    return []

def apply_modspec(spec_file: str):
    """
    Apply a modification specification file.
    
    Process:
    1. Extract command blocks (permissive grammar)
    2. Transform to canonical form
    3. Execute in git context with rollback support
    """
    with open(spec_file, 'r', encoding='utf-8') as f:
        content = f.read()
    blocks = extract_command_blocks(content)
    canonical_commands: List[Tuple[str, List[Any]]] = []
    for block in blocks:
        canonical_commands.extend(canonicalize_command(block))
    descriptions: List[str] = []
    touched: set = set()
    for cmd, modargs in canonical_commands:
        if cmd == 'modification_description':
            descriptions.append(modargs[0])
        else:
            touched.update(get_touched_files(cmd, modargs))
    desc = '\n'.join(descriptions).strip() or 'Automated modifications'
    prior_commit = run_git(['rev-parse', 'HEAD'])
    status = run_git(['status', '--porcelain'])
    has_tracked_changes = any((not line.startswith('??') for line in status.splitlines() if line))
    if has_tracked_changes:
        run_git(['add', '-u'])
        run_git(['commit', '-m', 'preparing to execute automated modifications'])
    try:
        for cmd, modargs in canonical_commands:
            if cmd != 'modification_description':
                execute_canonical(cmd, modargs)
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
        print('Usage: python modify_code.py <spec_file>')
        sys.exit(1)
    apply_modspec(sys.argv[1])