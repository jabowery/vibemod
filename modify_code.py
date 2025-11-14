import ast
import os
import re
import shutil
import sys
import subprocess


def run_git(cmd, check=True):
    result = subprocess.run(['git'] + cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"Git command failed: {result.stdout + result.stderr}")
    return result.stdout.strip()


def parse(file_content: str) -> list[tuple[str, list[str]]]:
    """
    Parse the ModSpec file content into blocks of (command, sections).
    """
    lines = file_content.splitlines(keepends=True)
    blocks = []
    i = 0
    header_re = re.compile(r'^\s*MMM\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+MMM\s*$')
    sep = '@@@@@@'
    escape = r'\\@@@@@@'
    
    while i < len(lines):
        line = lines[i]
        m = header_re.match(line)
        if m:
            cmd = m.group(1)
            i += 1
            body_lines = []
            while i < len(lines):
                next_line = lines[i]
                if header_re.match(next_line):
                    break
                body_lines.append(next_line)
                i += 1
            
            # Parse body into sections
            sections = []
            current_section = []
            for bl in body_lines:
                stripped = bl.strip()
                if stripped == sep:
                    sections.append(''.join(current_section))
                    current_section = []
                else:
                    # Apply escape replacement
                    bl = bl.replace(escape, sep)
                    current_section.append(bl)
            if current_section:
                sections.append(''.join(current_section))
            
            blocks.append((cmd, sections))
        else:
            i += 1  # Skip non-header lines
    return blocks


def is_valid_dotted(t: str) -> bool:
    name_re = r'[a-zA-Z_][a-zA-Z0-9_]*'
    pattern = rf'^{name_re}(\.{name_re})*$'
    return bool(re.match(pattern, t))


def parse_bool(s: str) -> bool:
    s = s.lower()
    if s in ['true', 'yes', 'y', '1']:
        return True
    elif s in ['false', 'no', 'n', '0']:
        return False
    else:
        raise ValueError(f"Invalid boolean: {s}")


def resolve(cmd: str, sections: list[str]):
    last_section = sections[-1]
    if last_section.strip()=='': 
        # This detects the tendency for LLMs to place a @@@@@@ at the end of a directive
        sections=sections[:-1] # drop trailing pure whitespace arguments
    arity = len(sections)
    print(cmd)
    print(sections[0])
    if cmd == 'modification_description':
        if arity != 1:
            raise ValueError(f"{cmd} requires arity 1")
        return [sections[0]]
    elif cmd in ['create_file', 'replace_file_contents']:
        if arity not in [2, 3]:
            raise ValueError(f"{cmd} requires arity 2-3")
        path = sections[0].strip()
        content = sections[1]
        make_exec = False
        if arity == 3:
            sections[2] = sections[2].strip()
            make_exec = sections[2] if sections[2] != '' else False
        return [path, content, make_exec]
    elif cmd == 'move_file':
        if arity != 2:
            raise ValueError(f"{cmd} requires arity 2")
        return [sections[0].strip(), sections[1].strip()]
    elif cmd == 'make_directory':
        if arity != 1:
            raise ValueError(f"{cmd} requires arity 1")
        return [sections[0].strip()]
    elif cmd == 'remove_file':
        if arity != 1:
            raise ValueError(f"{cmd} requires arity 1")
        return [sections[0].strip()]
    elif cmd == 'update_header':
        if arity != 2:
            raise ValueError(f"{cmd} requires arity 2")
        return [sections[0].strip(), sections[1]]
    elif cmd == 'declare':
        if arity == 2:
            # Schema A: shorthand
            combined = sections[0].strip()
            m = re.match(r'^(.+?\.py)\.(.+)$', combined)
            if not m:
                raise ValueError("Invalid shorthand for declare")
            file_path = m.group(1)
            dotted_target = m.group(2)
            content = sections[1]
            if not content.strip():
                raise ValueError("Content must be non-empty")
            if not is_valid_dotted(dotted_target):
                raise ValueError("Invalid dotted_target")
            return [file_path, dotted_target, content]
        elif arity == 3:
            # Schema B: explicit
            file_path = sections[0].strip()
            dotted_target = sections[1].strip()
            content = sections[2]
            if not content.strip():
                raise ValueError("Content must be non-empty")
            if not is_valid_dotted(dotted_target):
                raise ValueError("Invalid dotted_target")
            return [file_path, dotted_target, content]
        else:
            raise ValueError("declare requires arity 2 or 3")
    elif cmd == 'update_declaration':
        if arity != 3:
            raise ValueError("update_declaration requires arity 3")
        file_path = sections[0].strip()
        dotted_target = sections[1].strip()
        content = sections[2]
        if not content.strip():
            raise ValueError("Content must be non-empty")
        if not is_valid_dotted(dotted_target):
            raise ValueError("Invalid dotted_target")
        return [file_path, dotted_target, content]
    elif cmd == 'remove_declaration':
        if arity != 2:
            raise ValueError("remove_declaration requires arity 2")
        file_path = sections[0].strip()
        dotted_target = sections[1].strip()
        if not is_valid_dotted(dotted_target):
            raise ValueError("Invalid dotted_target")
        return [file_path, dotted_target]
    else:
        raise ValueError(f"Unknown command: {cmd}")


def get_scope(tree: ast.AST, parts: list[str]):
    current = tree
    for part in parts[:-1]:
        found = None
        for node in current.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == part:
                found = node
                break
        if found is None:
            return None
        current = found
    return current.body


def modify_declaration(file_path: str, dotted_target: str, content: str | None, remove: bool):
    if not os.path.exists(file_path):
        if remove:
            return
        else:
            raise FileNotFoundError(f"File not found: {file_path}")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()
    
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ValueError(f"AST parse error in {file_path}") from e
    
    parts = dotted_target.split('.')
    if not parts:
        raise ValueError("Invalid dotted_target")
    
    scope = get_scope(tree, parts)
    if scope is None:
        if remove:
            return
        else:
            raise ValueError(f"Parent scope not found for {dotted_target} in {file_path}")
    
    name = parts[-1]
    new_node = None
    if not remove:
        try:
            content_module = ast.parse(content)
        except SyntaxError as e:
            raise ValueError("Invalid content syntax") from e
        
        if not isinstance(content_module, ast.Module) or len(content_module.body) != 1:
            raise ValueError("Content must be a single declaration")
        
        decl = content_module.body[0]
        if not isinstance(decl, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            raise ValueError("Content must be a class or function definition")
        
        if decl.name != name:
            raise ValueError(f"Name mismatch: expected {name}, got {decl.name}")
        
        new_node = decl
    
    # Find existing declarations
    existing_indices = []
    for i, node in enumerate(scope):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            existing_indices.append(i)
    
    # Remove existing
    for i in sorted(existing_indices, reverse=True):
        del scope[i]
    
    if not remove:
        # Determine insertion position
        pos = len(scope)
        if existing_indices:
            pos = min(existing_indices)
        else:
            for i, node in enumerate(scope):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    pos = i
                    break
        
        scope.insert(pos, new_node)
    
    # Write back
    new_source = ast.unparse(tree)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_source + '\n')


def execute(cmd: str, args):
    if cmd == 'modification_description':
        pass  # Descriptive, no action
    elif cmd in ['create_file', 'replace_file_contents']:
        path, content, make_exec = args
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        if make_exec:
            if not sys.platform.startswith('win'):
                os.chmod(path, 0o755)
    elif cmd == 'move_file':
        src, dst = args
        shutil.move(src, dst)
    elif cmd == 'make_directory':
        path = args[0]
        os.makedirs(path)
    elif cmd == 'remove_file':
        path = args[0]
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.isfile(path):
            os.remove(path)
        else:
            raise FileNotFoundError(f"No such file or directory: {path}")
    elif cmd == 'update_header':
        file_path, new_header = args
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            raise ValueError(f"AST parse error in {file_path}") from e
        decl_nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
        if not decl_nodes:
            # No declarations, replace whole file
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


def apply_modspec(spec_file: str):
    with open(spec_file, 'r', encoding='utf-8') as f:
        content = f.read()
    blocks = parse(content)
    
    descriptions = []
    touched = set()
    for cmd, sections in blocks:
        args = resolve(cmd, sections)
        if cmd == 'modification_description':
            descriptions.append(args[0])
        else:
            if cmd in ['create_file', 'replace_file_contents', 'make_directory', 'remove_file', 'update_header', 'declare', 'update_declaration', 'remove_declaration']:
                touched.add(args[0])
            elif cmd == 'move_file':
                touched.add(args[0])
                touched.add(args[1])
    
    desc = '\n'.join(descriptions).strip()
    if not desc:
        desc = "Automated modifications"
    
    # Git preparation
    prior_commit = run_git(['rev-parse', 'HEAD'])
    status = run_git(['status', '--porcelain'])
    has_tracked_changes = any(not line.startswith('??') for line in status.splitlines() if line)
    if has_tracked_changes:
        run_git(['add', '-u'])
        run_git(['commit', '-m', 'preparing to execute automated modifications'])
    
    try:
        # Execute non-description commands
        for cmd, sections in blocks:
            if cmd != 'modification_description':
                args = resolve(cmd, sections)
                execute(cmd, args)
        
        # Stage changes for touched paths
        for path in touched:
            run_git(['add', path], check=False)
        
        # Commit changes if any
        try:
            run_git(['commit', '-m', desc])
        except RuntimeError as e:
            if 'nothing to commit' in str(e):
                pass
            else:
                raise
    except Exception as e:
        run_git(['reset', '--hard', prior_commit])
        raise


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python -m modspec <spec_file>")
        sys.exit(1)
    apply_modspec(sys.argv[1])
