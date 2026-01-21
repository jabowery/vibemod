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
    """
    Rust-specific declaration modification using tree-sitter.

    Implements the vibemod Rust specification:
    - Multi-match replace/remove (replaces ALL matches by default)
    - Insertion anchors for new items
    - Rich error diagnostics
    - Single declaration validation
    - Uniqueness validation to prevent illegal duplicates
    - Syntax validation to prevent malformed code
    - Debug dump on syntax errors for easier troubleshooting
    - Smart attribute handling: preserves original attributes if replacement lacks them
    - Tolerant method declaration: unwraps impl blocks when declaring methods
    """
    from .handlers.rust_handler import RustHandler, parse_target_path, RUST_DECL_TYPES
    handler = RustHandler()
    target = parse_target_path(dotted_target)
    source_before = source

    def validate_and_write(new_source: str):
        """Validate and write the new source, or raise with diagnostics."""
        syntax_error = handler.validate_syntax(new_source, original_content=source_before)
        if syntax_error:
            debug_dir = _dump_syntax_error_debug(file_path=file_path, target_path=dotted_target, content=content, source_before=source_before, source_after=new_source, error_message=syntax_error, remove=remove)
            raise ValueError(f'Modification would create syntactically invalid Rust code:\n{syntax_error}\n\nDebug files written to: {debug_dir}')
        dup_error = handler.validate_no_illegal_duplicates(new_source)
        if dup_error:
            raise ValueError(f'Modification would create invalid Rust code:\n{dup_error}')
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_source)

    def content_starts_with_attr_or_doc(code: str) -> bool:
        """Check if code starts with attributes (#[...]) or doc comments (///)."""
        stripped = code.lstrip()
        return stripped.startswith('#[') or stripped.startswith('///') or stripped.startswith('//!') or stripped.startswith('/**') or stripped.startswith('/*!')

    def byte_to_char_offset(text: str, byte_offset: int) -> int:
        """Convert UTF-8 byte offset to Python character offset."""
        if byte_offset <= 0:
            return 0
        encoded = text.encode('utf-8')
        if byte_offset >= len(encoded):
            return len(text)
        return len(encoded[:byte_offset].decode('utf-8'))

    def find_decl_start_in_span(span_text: str) -> int:
        """
        Find where the actual declaration starts in a span that may include
        doc comments and attributes.

        Returns the character offset within span_text where the declaration keyword
        (pub, fn, struct, enum, impl, etc.) begins.
        """
        temp_root = handler._parse(span_text)
        for child in temp_root.children:
            if child.type in RUST_DECL_TYPES:
                return byte_to_char_offset(span_text, child.start_byte)
            if child.type in ('line_comment', 'block_comment', 'attribute_item'):
                continue
            if child.type not in ('line_comment', 'block_comment', 'attribute_item'):
                return byte_to_char_offset(span_text, child.start_byte)
        return 0

    def adjust_span_for_attributes(span_start: int, span_end: int, new_content: str) -> tuple[int, int]:
        """
        Adjust span if original has doc comments/attributes but replacement doesn't.

        If the original span includes doc comments/attributes but the new content
        doesn't start with them, adjust the span to preserve the original 
        doc comments and attributes, replacing only the declaration itself.
        """
        if content_starts_with_attr_or_doc(new_content):
            return (span_start, span_end)
        original_span_text = source[span_start:span_end]
        if not content_starts_with_attr_or_doc(original_span_text):
            return (span_start, span_end)
        decl_offset = find_decl_start_in_span(original_span_text)
        if decl_offset > 0:
            return (span_start + decl_offset, span_end)
        return (span_start, span_end)
    if content is not None and target.is_impl_target and target.associated_name:
        unwrapped = handler.unwrap_method_from_impl(content, target.associated_name)
        if unwrapped is not None:
            content = unwrapped
    if remove:
        spans = handler.find_all_declarations(source, dotted_target)
        if not spans:
            return
        spans.sort(key=lambda s: s[0], reverse=True)
        new_source = source
        for start, end in spans:
            before = new_source[:start].rstrip()
            after = new_source[end:].lstrip()
            new_source = before + '\n\n' + after
        new_source = re.sub('\\n{3,}', '\n\n', new_source)
        validate_and_write(new_source)
        return
    if content is None:
        raise ValueError('Content required for declare operation')
    content = textwrap.dedent(content).strip()
    single_decl_error = handler.validate_single_declaration(content)
    if single_decl_error:
        raise ValueError(f'Invalid declare content:\n{single_decl_error}')
    if target.is_insertion:
        insertion_point = handler.get_insertion_point(source, dotted_target)
        if insertion_point is None:
            diagnostic = handler.format_candidates_diagnostic(source, dotted_target)
            raise ValueError(f'Cannot determine insertion point.\n{diagnostic}')
        before = source[:insertion_point].rstrip()
        after = source[insertion_point:].lstrip()
        new_source = before + '\n\n' + content + '\n\n' + after
        new_source = re.sub('\\n{3,}', '\n\n', new_source)
        validate_and_write(new_source)
        return
    if target.is_impl_target and target.associated_name:
        spans = handler.find_all_declarations(source, dotted_target)
        if spans:
            adjusted_spans = [adjust_span_for_attributes(s, e, content) for s, e in spans]
            adjusted_spans.sort(key=lambda s: s[0], reverse=True)
            new_source = source
            for start, end in adjusted_spans:
                new_source = new_source[:start] + content + new_source[end:]
            validate_and_write(new_source)
            return
        insertion_point = handler.get_impl_block_insertion_point(source, dotted_target)
        if insertion_point is None:
            diagnostic = handler.format_candidates_diagnostic(source, dotted_target)
            raise ValueError(f"Cannot insert method '{target.associated_name}': no matching impl block found or multiple impl blocks match (use @N selector).\n{diagnostic}")
        line_start = source.rfind('\n', 0, insertion_point) + 1
        line_content = source[line_start:insertion_point]
        base_indent = len(line_content) - len(line_content.lstrip())
        indent = ' ' * (base_indent + 4)
        indented_content = '\n'.join((indent + line if line.strip() else line for line in content.split('\n')))
        new_source = source[:insertion_point] + '\n' + indented_content + '\n' + source[insertion_point:]
        validate_and_write(new_source)
        return
    spans = handler.find_all_declarations(source, dotted_target)
    if spans:
        adjusted_spans = [adjust_span_for_attributes(s, e, content) for s, e in spans]
        adjusted_spans.sort(key=lambda s: s[0], reverse=True)
        new_source = source
        for start, end in adjusted_spans:
            new_source = new_source[:start] + content + new_source[end:]
        validate_and_write(new_source)
        return
    if target.is_impl_target and (not target.associated_name):
        diagnostic = handler.format_candidates_diagnostic(source, dotted_target)
        raise ValueError(f"No impl block found for '{dotted_target}'. To add a new impl block, use an insertion anchor like @append_file.\n{diagnostic}")
    new_source = source.rstrip() + '\n\n' + content + '\n' if source.strip() else content + '\n'
    validate_and_write(new_source)

def _modify_declaration_python(file_path: str, source: str, dotted_target: str, content: str | None, remove: bool):
    """Python-specific declaration modification using AST."""
    if remove:
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            raise ValueError(f'AST parse error in {file_path}') from e
        parts = dotted_target.split('.')
        if not parts:
            raise ValueError('Invalid dotted_target')
        scope = get_scope(tree, parts[:-1]) if len(parts) > 1 else tree
        if scope is None:
            return
        name = parts[-1]
        body_to_modify = scope.body if hasattr(scope, 'body') else scope
        existing_indices = []
        for i, node in enumerate(body_to_modify):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                existing_indices.append(i)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        existing_indices.append(i)
                        break
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == name:
                    existing_indices.append(i)
        if not existing_indices:
            return
        for i in sorted(existing_indices, reverse=True):
            del body_to_modify[i]
        new_source = ast.unparse(tree) + '\n'
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_source)
        return
    if content is None:
        raise ValueError('Content required for declare operation')
    from .handlers.python_handler import PythonHandler
    handler = PythonHandler()
    single_decl_error = handler.validate_single_declaration(content)
    if single_decl_error:
        raise ValueError(f'Invalid declare content:\n{single_decl_error}')
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
        raise ValueError(f'Parent scope not found for {dotted_target} in {file_path}')
    name = parts[-1]
    new_node = None
    is_assignment = False
    content = textwrap.dedent(content)
    try:
        content_module = ast.parse(content)
    except SyntaxError as e:
        raise ValueError('Invalid content syntax') from e
    body = content_module.body
    imports_to_add = []
    while body and isinstance(body[0], (ast.Import, ast.ImportFrom)):
        imports_to_add.append(body.pop(0))
    while body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body.pop(0)
    if len(body) != 1:
        raise ValueError('Content must be a single declaration, optionally preceded by import statements')
    decl = body[0]
    if isinstance(decl, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        if decl.name != name:
            raise ValueError(f'Name mismatch: expected {name}, got {decl.name}')
        new_node = decl
    elif isinstance(decl, (ast.Assign, ast.AnnAssign)):
        assign_names = handler._get_assignment_names(decl)
        if name not in assign_names:
            raise ValueError(f'Name mismatch: expected {name}, got {assign_names}')
        new_node = decl
        is_assignment = True
    else:
        raise ValueError('Content must be a class, function definition, or assignment')
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
    existing_indices = []
    for i, node in enumerate(body_to_modify):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            existing_indices.append(i)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    existing_indices.append(i)
                    break
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                existing_indices.append(i)
    for i in sorted(existing_indices, reverse=True):
        del body_to_modify[i]
    if new_node is not None:
        pos = len(body_to_modify)
        if existing_indices:
            pos = min(existing_indices)
        elif not is_assignment:
            for i, node in enumerate(body_to_modify):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    pos = i
                    break
        else:
            pos = 0
            for i, node in enumerate(body_to_modify):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    pos = i + 1
                elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    pos = i + 1
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    pos = i + 1
                else:
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

def _dump_syntax_error_debug(file_path: str, target_path: str, content: str | None, source_before: str, source_after: str, error_message: str, remove: bool=False) -> str:
    """
    Dump debugging information when a syntax error is detected.
    
    Creates a timestamped subdirectory under "syntax_errors/" with:
    - directives.txt: The directive being processed
    - before.rs: File content before modification
    - after.rs: Attempted content after modification
    - error.txt: The error message
    
    Returns the path to the created directory.
    """
    from datetime import datetime
    debug_dir = os.path.join(os.getcwd(), 'syntax_errors')
    os.makedirs(debug_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    safe_target = target_path.replace('/', '_').replace('.', '_').replace(':', '_')[:50]
    subdir_name = f'{timestamp}_{safe_target}'
    subdir = os.path.join(debug_dir, subdir_name)
    os.makedirs(subdir, exist_ok=True)
    _, ext = os.path.splitext(file_path)
    if not ext:
        ext = '.rs'
    directive_type = 'remove_declaration' if remove else 'declare'
    directives_content = f'MMM {directive_type} MMM\n{file_path}\n@@@@@@\n{target_path}\n'
    if content is not None and (not remove):
        directives_content += f'@@@@@@\n{content}\n'
    with open(os.path.join(subdir, 'directives.txt'), 'w', encoding='utf-8') as f:
        f.write(directives_content)
    with open(os.path.join(subdir, f'before{ext}'), 'w', encoding='utf-8') as f:
        f.write(source_before)
    with open(os.path.join(subdir, f'after{ext}'), 'w', encoding='utf-8') as f:
        f.write(source_after)
    error_content = f'Syntax Error Debug Dump\n=======================\n\nFile: {file_path}\nTarget: {target_path}\nOperation: {directive_type}\nTimestamp: {datetime.now().isoformat()}\n\nError Message:\n{error_message}\n\nFiles in this directory:\n- directives.txt: The MMM directive that caused this error\n- before{ext}: The file content before the modification attempt\n- after{ext}: The resulting content that failed validation\n'
    with open(os.path.join(subdir, 'error.txt'), 'w', encoding='utf-8') as f:
        f.write(error_content)
    return subdir

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
    Modify a declaration in a source file using language-specific handlers.

    Supports Python (.py) and Rust (.rs) files via the handler registry.
    """
    from .handlers import get_handler
    file_exists = os.path.exists(file_path)
    if not file_exists:
        if remove:
            return
        source = None
    else:
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
    ext = os.path.splitext(file_path)[1]
    if ext == '.py':
        if not file_exists:
            raise FileNotFoundError(f'File not found: {file_path}')
        _modify_declaration_python(file_path, source, dotted_target, content, remove)
    elif ext == '.rs':
        _modify_declaration_rust(file_path, source or '', dotted_target, content, remove)
    else:
        handler = get_handler(file_path)
        raise ValueError(f'Declaration modification not yet implemented for {ext}')

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