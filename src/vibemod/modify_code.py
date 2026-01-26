import ast
import os
import re
import shutil
import sys
import subprocess
import textwrap
from dataclasses import dataclass
from typing import List, Tuple, Any

def _execute_update_header(file_path: str, new_header: str):
    """Execute update_header command with language-aware dispatch."""
    from .handlers import get_handler

    if not os.path.exists(file_path):
        raise FileNotFoundError(f'File not found: {file_path}')

    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()

    handler = get_handler(file_path)
    new_source = handler.update_header(source, new_header)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_source)

def run_git(cmd, check=True):
    result = subprocess.run(['git'] + cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f'Git command failed: {result.stdout + result.stderr}')
    return result.stdout.strip()
HEADER_RE_PERMISSIVE = re.compile('^\\s*MMM\\s+([a-zA-Z_][a-zA-Z0-9_]*)\\s+MMM\\b.*$')
SEP = '@@@@@@'
ESCAPE = '\\@@@@@@'

def _dump_syntax_error_debug(file_path: str, target_path: str, content: str, source_before: str, source_after: str, error_message: str, remove: bool) -> str:
    """
    Dump debug information when a syntax error is detected.

    Returns the path to the debug directory.
    """
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    safe_target = re.sub(r'[^\w\-.]', '_', target_path)[:50]
    dir_name = f'{timestamp}_{safe_target}'

    debug_dir = os.path.join('/tmp', 'vibemod_syntax_errors', dir_name)
    os.makedirs(debug_dir, exist_ok=True)

    print(f'Syntax error debug files written to: {debug_dir}', file=sys.stderr)

    ext = os.path.splitext(file_path)[1] or '.txt'

    directive_file = os.path.join(debug_dir, 'directives.txt')
    op = 'remove_declaration' if remove else 'declare'
    with open(directive_file, 'w', encoding='utf-8') as f:
        f.write(f'MMM {op} MMM\n')
        f.write(f'{file_path}\n')
        f.write('@@@@@@\n')
        f.write(f'{target_path}\n')
        f.write('@@@@@@\n')
        if content:
            f.write(content)
            if not content.endswith('\n'):
                f.write('\n')

    before_file = os.path.join(debug_dir, f'before{ext}')
    with open(before_file, 'w', encoding='utf-8') as f:
        f.write(source_before)

    after_file = os.path.join(debug_dir, f'after{ext}')
    with open(after_file, 'w', encoding='utf-8') as f:
        f.write(source_after)

    error_file = os.path.join(debug_dir, 'error.txt')
    with open(error_file, 'w', encoding='utf-8') as f:
        f.write('Syntax Error Debug Dump\n')
        f.write('=======================\n\n')
        f.write(f'File: {file_path}\n')
        f.write(f'Target: {target_path}\n')
        f.write(f'Operation: {op}\n')
        f.write(f'Timestamp: {datetime.now().isoformat()}\n\n')
        f.write('Error Message:\n')
        f.write(error_message)
        f.write('\n\nFiles in this directory:\n')
        f.write('- directives.txt: The MMM directive that caused this error\n')
        f.write(f'- before{ext}: The file content before the modification attempt\n')
        f.write(f'- after{ext}: The resulting content that failed validation\n')

    return debug_dir

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
    quirk_commands = ['create_file', 'replace_file_contents', 'update_file', 'replace_file', 'replace_file_contents', 'move_file', 'make_directory', 'remove_file', 'declare', 'update_declaration', 'remove_declaration', 'append_file']
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
    elif cmd == 'append_file':
        if arity < 2 or arity > 3:
            raise ValueError(f'{cmd} requires 2 or 3 arguments but got {arity}')
        path = sections[0].strip()
        content = sections[1]
        idempotent = True
        if arity == 3:
            flag_str = sections[2].strip()
            idempotent = parse_bool(flag_str)
        return [('append_file', [path, content, idempotent])]
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
    Modify a declaration in a source file.

    Delegates to the appropriate language handler based on file extension.
    """
    from .handlers import get_handler
    file_exists = os.path.exists(file_path)
    if remove and (not file_exists):
        return
    if not file_exists:
        source = ''
    else:
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
    handler = get_handler(file_path)
    new_source = handler.modify_declaration(file_path=file_path, source=source, target_path=dotted_target, content=content, remove=remove, debug_dump_func=_dump_syntax_error_debug)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_source)

def execute_canonical(cmd: str, modargs: List[Any]):
    """
    Execute a command in canonical form.

    All commands must be in their canonical form at this point:
    - modification_description(description)
    - create_file(path, content, make_exec)
    - append_file(path, content, idempotent)
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
    elif cmd == 'append_file':
        path, content, idempotent = modargs
        existing_content = ''
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                existing_content = f.read()
            if idempotent:
                content_stripped = content.rstrip()
                existing_stripped = existing_content.rstrip()
                if existing_stripped.endswith(content_stripped):
                    return
            if existing_content and (not existing_content.endswith('\n')):
                content = '\n' + content
            with open(path, 'a', encoding='utf-8') as f:
                f.write(content)
        else:
            os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
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
    if cmd in ['create_file', 'append_file', 'make_directory', 'remove_file', 'update_header', 'declare', 'remove_declaration']:
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
        print(f'canonicalize: {block.command}')
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
