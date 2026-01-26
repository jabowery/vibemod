import pytest
import os
import tempfile
import shutil
from vibemod.modify_code import apply_modspec


@pytest.fixture
def temp_dir():
    dir_path = tempfile.mkdtemp()
    yield dir_path
    shutil.rmtree(dir_path)


@pytest.fixture
def create_latex_file(temp_dir):
    def _create(filename, content):
        path = os.path.join(temp_dir, filename)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path
    return _create


@pytest.fixture
def create_spec_file(temp_dir):
    def _create(content):
        path = os.path.join(temp_dir, 'spec.mmm')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path
    return _create


def test_declare_replace_section(create_latex_file, create_spec_file):
    latex_path = create_latex_file('test.tex', r'''
\documentclass{article}
\begin{document}
\section{Old Section}
\end{document}
''')
    spec_content = rf'''
MMM declare MMM
{latex_path}
@@@@@@
section
@@@@@@
\section{{New Section}}
'''
    spec_path = create_spec_file(spec_content)
    apply_modspec(spec_path)
    with open(latex_path, 'r', encoding='utf-8') as f:
        modified = f.read()
    assert r'\section{New Section}' in modified
    assert r'\section{Old Section}' not in modified


def test_declare_insert_append(create_latex_file, create_spec_file):
    latex_path = create_latex_file('test.tex', r'''
\documentclass{article}
\begin{document}
\section{Existing}
\end{document}
''')
    spec_content = rf'''
MMM declare MMM
{latex_path}
@@@@@@
section@append
@@@@@@
\section{{Appended Section}}
'''
    spec_path = create_spec_file(spec_content)
    apply_modspec(spec_path)
    with open(latex_path, 'r', encoding='utf-8') as f:
        modified = f.read()
    assert r'\section{Existing}' in modified
    assert r'\section{Appended Section}' in modified


def test_remove_declaration(create_latex_file, create_spec_file):
    latex_path = create_latex_file('test.tex', r'''
\documentclass{article}
\begin{document}
\section{To Remove}
\end{document}
''')
    spec_content = rf'''
MMM remove_declaration MMM
{latex_path}
@@@@@@
section
'''
    spec_path = create_spec_file(spec_content)
    apply_modspec(spec_path)
    with open(latex_path, 'r', encoding='utf-8') as f:
        modified = f.read()
    assert r'\section{To Remove}' not in modified


def test_update_header(create_latex_file, create_spec_file):
    latex_path = create_latex_file('test.tex', r'''
\documentclass{article}
\usepackage{amsmath}
\begin{document}
Content
\end{document}
''')
    spec_content = rf'''
MMM update_header MMM
{latex_path}
@@@@@@
\documentclass{{book}}
\usepackage{{graphicx}}
'''
    spec_path = create_spec_file(spec_content)
    apply_modspec(spec_path)
    with open(latex_path, 'r', encoding='utf-8') as f:
        modified = f.read()
    assert r'\documentclass{book}' in modified
    assert r'\usepackage{graphicx}' in modified
    assert r'\usepackage{amsmath}' not in modified


def test_declare_nested_item(create_latex_file, create_spec_file):
    latex_path = create_latex_file('test.tex', r'''
\documentclass{article}
\begin{document}
\begin{itemize}
\item Old Item
\end{itemize}
\end{document}
''')
    spec_content = rf'''
MMM declare MMM
{latex_path}
@@@@@@
begin.itemize.item
@@@@@@
\item New Item
'''
    spec_path = create_spec_file(spec_content)
    apply_modspec(spec_path)
    with open(latex_path, 'r', encoding='utf-8') as f:
        modified = f.read()
    assert r'\item New Item' in modified
    assert r'\item Old Item' not in modified


def test_invalid_syntax_rejected(create_latex_file, create_spec_file):
    latex_path = create_latex_file('test.tex', r'''
\documentclass{article}
\begin{document}
\end{document}
''')
    spec_content = rf'''
MMM declare MMM
{latex_path}
@@@@@@
section
@@@@@@
\section{{Unbalanced
'''
    spec_path = create_spec_file(spec_content)
    with pytest.raises(ValueError, match='invalid|syntax|error'):
        apply_modspec(spec_path)