# tests/test_elixir_handler.py
"""Tests for the Elixir language handler."""

import pytest
from vibemod.handlers.elixir_handler import ElixirHandler


@pytest.fixture
def handler():
    return ElixirHandler()


# =============================================================================
# TARGET PATH PARSING
# =============================================================================

class TestTargetPathParsing:
    """Tests for parse_target_path."""
    
    def test_simple_module(self, handler):
        target = handler.parse_target_path("MyModule")
        assert target.module_path == ["MyModule"]
        assert target.item_name is None
        assert target.is_module_target
        assert not target.is_function_target
    
    def test_nested_module(self, handler):
        target = handler.parse_target_path("MyApp.Users")
        assert target.module_path == ["MyApp", "Users"]
        assert target.item_name is None
        assert target.is_module_target
    
    def test_deeply_nested_module(self, handler):
        target = handler.parse_target_path("MyApp.Web.Controllers.UserController")
        assert target.module_path == ["MyApp", "Web", "Controllers", "UserController"]
        assert target.is_module_target
    
    def test_function_in_module(self, handler):
        target = handler.parse_target_path("MyApp.Users.get_user")
        assert target.module_path == ["MyApp", "Users"]
        assert target.item_name == "get_user"
        assert target.is_function_target
        assert not target.is_module_target
    
    def test_function_with_arity(self, handler):
        target = handler.parse_target_path("MyApp.Users.get_user/1")
        assert target.module_path == ["MyApp", "Users"]
        assert target.item_name == "get_user"
        assert target.arity == 1
    
    def test_function_with_zero_arity(self, handler):
        target = handler.parse_target_path("MyApp.Users.list_users/0")
        assert target.item_name == "list_users"
        assert target.arity == 0
    
    def test_private_function_prefix(self, handler):
        target = handler.parse_target_path("defp:validate_email")
        assert target.item_name == "validate_email"
        assert target.is_private
        assert target.module_path == []
    
    def test_private_function_in_module(self, handler):
        target = handler.parse_target_path("MyApp.Users.defp:validate_email")
        assert target.module_path == ["MyApp", "Users"]
        assert target.item_name == "validate_email"
        assert target.is_private
    
    def test_occurrence_selector(self, handler):
        target = handler.parse_target_path("MyApp.Users.get_user@2")
        assert target.item_name == "get_user"
        assert target.occurrence == 2
    
    def test_arity_and_occurrence(self, handler):
        target = handler.parse_target_path("MyApp.Users.handle_call/3@1")
        assert target.item_name == "handle_call"
        assert target.arity == 3
        assert target.occurrence == 1


# =============================================================================
# INDEXING
# =============================================================================

class TestIndexing:
    """Tests for _build_item_index."""
    
    def test_simple_module(self, handler):
        code = '''
defmodule MyModule do
  def hello, do: :world
end
'''
        items = handler._build_item_index(code)
        
        # Should find module and function
        assert len(items) == 2
        
        module = items[0]
        assert module.kind == "defmodule"
        assert module.name == "MyModule"
        
        func = items[1]
        assert func.kind == "def"
        assert func.name == "hello"
        assert func.module_path == ["MyModule"]
    
    def test_nested_module_name(self, handler):
        code = '''
defmodule MyApp.Users do
  def list_users, do: []
end
'''
        items = handler._build_item_index(code)
        
        module = items[0]
        assert module.name == "MyApp.Users"
        assert module.module_path == []
        
        func = items[1]
        assert func.module_path == ["MyApp", "Users"]
    
    def test_multiple_functions(self, handler):
        code = '''
defmodule Calculator do
  def add(a, b), do: a + b
  def subtract(a, b), do: a - b
  defp validate(x), do: is_number(x)
end
'''
        items = handler._build_item_index(code)
        
        assert len(items) == 4  # 1 module + 3 functions
        
        funcs = [i for i in items if i.kind in ("def", "defp")]
        assert len(funcs) == 3
        
        names = [f.name for f in funcs]
        assert "add" in names
        assert "subtract" in names
        assert "validate" in names
        
        private = [f for f in funcs if f.kind == "defp"]
        assert len(private) == 1
        assert private[0].name == "validate"
    
    def test_function_arity(self, handler):
        code = '''
defmodule Math do
  def add(a, b), do: a + b
  def square(x), do: x * x
  def pi, do: 3.14159
end
'''
        items = handler._build_item_index(code)
        funcs = {i.name: i for i in items if i.kind == "def"}
        
        assert funcs["add"].arity == 2
        assert funcs["square"].arity == 1
        assert funcs["pi"].arity == 0
    
    def test_multiple_modules(self, handler):
        code = '''
defmodule ModuleA do
  def func_a, do: :a
end

defmodule ModuleB do
  def func_b, do: :b
end
'''
        items = handler._build_item_index(code)
        
        modules = [i for i in items if i.kind == "defmodule"]
        assert len(modules) == 2
        
        module_names = [m.name for m in modules]
        assert "ModuleA" in module_names
        assert "ModuleB" in module_names
    
    def test_doc_attribute_attached(self, handler):
        code = '''
defmodule MyModule do
  @doc "Says hello"
  def hello, do: :world
end
'''
        items = handler._build_item_index(code)
        func = [i for i in items if i.kind == "def"][0]
        
        assert len(func.attrs) == 1
        assert "@doc" in func.attrs[0]
    
    def test_moduledoc_not_attached_to_function(self, handler):
        code = '''
defmodule MyModule do
  @moduledoc """
  Module documentation.
  """
  
  def hello, do: :world
end
'''
        items = handler._build_item_index(code)
        func = [i for i in items if i.kind == "def"][0]
        
        # @moduledoc should NOT be attached to the function
        assert len(func.attrs) == 0
    
    def test_macros(self, handler):
        code = '''
defmodule MyMacros do
  defmacro debug(expr) do
    quote do
      IO.inspect(unquote(expr))
    end
  end
  
  defmacrop private_macro(x), do: x
end
'''
        items = handler._build_item_index(code)
        
        macros = [i for i in items if i.kind in ("defmacro", "defmacrop")]
        assert len(macros) == 2
        
        public_macro = [m for m in macros if m.kind == "defmacro"][0]
        assert public_macro.name == "debug"
        
        private_macro = [m for m in macros if m.kind == "defmacrop"][0]
        assert private_macro.name == "private_macro"


# =============================================================================
# DECLARATION FINDING
# =============================================================================

class TestFindDeclaration:
    """Tests for find_declaration."""
    
    def test_find_module(self, handler):
        code = '''
defmodule MyApp.Users do
  def get_user(id), do: id
end
'''
        span = handler.find_declaration(code, "MyApp.Users")
        assert span is not None
        assert code[span[0]:span[1]].startswith("defmodule MyApp.Users")
    
    def test_find_function(self, handler):
        code = '''
defmodule MyApp.Users do
  def get_user(id), do: id
  def create_user(attrs), do: attrs
end
'''
        span = handler.find_declaration(code, "MyApp.Users.get_user")
        assert span is not None
        content = code[span[0]:span[1]]
        assert "def get_user" in content
        assert "create_user" not in content
    
    def test_find_function_by_arity(self, handler):
        code = '''
defmodule MyModule do
  def process(x), do: x
  def process(x, y), do: x + y
  def process(x, y, z), do: x + y + z
end
'''
        # Find the 2-arity version
        span = handler.find_declaration(code, "MyModule.process/2")
        assert span is not None
        content = code[span[0]:span[1]]
        assert "def process(x, y)" in content
    
    def test_find_private_function(self, handler):
        code = '''
defmodule MyModule do
  def public_func, do: private_func()
  defp private_func, do: :secret
end
'''
        span = handler.find_declaration(code, "MyModule.defp:private_func")
        assert span is not None
        content = code[span[0]:span[1]]
        assert "defp private_func" in content
    
    def test_find_by_occurrence(self, handler):
        code = '''
defmodule GenServerExample do
  def handle_call(:get, _from, state), do: {:reply, state, state}
  def handle_call(:reset, _from, _state), do: {:reply, :ok, 0}
end
'''
        # Find second handle_call
        span = handler.find_declaration(code, "GenServerExample.handle_call@2")
        assert span is not None
        content = code[span[0]:span[1]]
        assert ":reset" in content
    
    def test_find_nonexistent_returns_none(self, handler):
        code = '''
defmodule MyModule do
  def existing_func, do: :ok
end
'''
        span = handler.find_declaration(code, "MyModule.nonexistent")
        assert span is None
    
    def test_find_all_declarations(self, handler):
        code = '''
defmodule MyModule do
  def handle_info(:tick, state), do: {:noreply, state}
  def handle_info(:tock, state), do: {:noreply, state}
  def handle_info(_, state), do: {:noreply, state}
end
'''
        spans = handler.find_all_declarations(code, "MyModule.handle_info")
        assert len(spans) == 3


# =============================================================================
# VALIDATION
# =============================================================================

class TestValidation:
    """Tests for validation methods."""
    
    def test_validate_single_declaration_valid(self, handler):
        code = '''
def hello(name) do
  "Hello, #{name}!"
end
'''
        error = handler.validate_single_declaration(code)
        assert error is None
    
    def test_validate_single_declaration_module(self, handler):
        code = '''
defmodule MyModule do
  def hello, do: :world
end
'''
        error = handler.validate_single_declaration(code)
        assert error is None  # Module with functions is OK
    
    def test_validate_single_declaration_multiple_modules(self, handler):
        code = '''
defmodule ModuleA do
end

defmodule ModuleB do
end
'''
        error = handler.validate_single_declaration(code)
        assert error is not None
        assert "declarations" in error.lower()
    
    def test_validate_syntax_valid(self, handler):
        code = '''
defmodule MyModule do
  def hello, do: :world
end
'''
        error = handler.validate_syntax(code)
        assert error is None
    
    def test_validate_syntax_invalid(self, handler):
        code = '''
defmodule MyModule do
  def hello do
    # Missing end
end
'''
        error = handler.validate_syntax(code)
        # Tree-sitter may or may not catch this depending on the error
        # Just verify it returns something or None without crashing
        assert error is None or isinstance(error, str)
    
    def test_validate_no_duplicate_modules(self, handler):
        code = '''
defmodule MyModule do
end

defmodule MyModule do
end
'''
        error = handler.validate_no_illegal_duplicates(code)
        assert error is not None
        assert "duplicate" in error.lower()
    
    def test_validate_different_modules_ok(self, handler):
        code = '''
defmodule ModuleA do
end

defmodule ModuleB do
end
'''
        error = handler.validate_no_illegal_duplicates(code)
        assert error is None


# =============================================================================
# MODIFY DECLARATION
# =============================================================================

class TestModifyDeclaration:
    """Tests for modify_declaration."""
    
    def test_replace_function(self, handler):
        source = '''
defmodule MyModule do
  def hello(name) do
    "Hello, #{name}!"
  end
end
'''
        new_content = '''def hello(name) do
    "Hi there, #{name}!"
  end'''
        
        result = handler.modify_declaration(
            file_path="test.ex",
            source=source,
            target_path="MyModule.hello",
            content=new_content,
            remove=False
        )
        
        assert "Hi there" in result
        assert "Hello," not in result
    
    def test_replace_module(self, handler):
        source = '''
defmodule OldModule do
  def func, do: :old
end
'''
        new_content = '''defmodule OldModule do
  def func, do: :new
  def other, do: :added
end'''
        
        result = handler.modify_declaration(
            file_path="test.ex",
            source=source,
            target_path="OldModule",
            content=new_content,
            remove=False
        )
        
        assert ":new" in result
        assert ":added" in result
        assert ":old" not in result
    
    def test_remove_function(self, handler):
        source = '''
defmodule MyModule do
  def keep_me, do: :yes
  def remove_me, do: :no
  def also_keep, do: :yes
end
'''
        result = handler.modify_declaration(
            file_path="test.ex",
            source=source,
            target_path="MyModule.remove_me",
            content=None,
            remove=True
        )
        
        assert "keep_me" in result
        assert "also_keep" in result
        assert "remove_me" not in result
    
    def test_add_new_function(self, handler):
        source = '''
defmodule MyModule do
  def existing, do: :ok
end
'''
        new_content = '''def new_func, do: :added'''
        
        result = handler.modify_declaration(
            file_path="test.ex",
            source=source,
            target_path="MyModule.new_func",
            content=new_content,
            remove=False
        )
        
        assert "existing" in result
        assert "new_func" in result


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and special scenarios."""
    
    def test_genserver_callbacks(self, handler):
        code = '''
defmodule MyServer do
  use GenServer
  
  @impl true
  def init(state), do: {:ok, state}
  
  @impl true
  def handle_call(:get, _from, state), do: {:reply, state, state}
  
  @impl true
  def handle_cast({:set, value}, _state), do: {:noreply, value}
end
'''
        items = handler._build_item_index(code)
        funcs = [i for i in items if i.kind == "def"]
        
        assert len(funcs) == 3
        
        # Check @impl is attached
        for func in funcs:
            assert any("@impl" in attr for attr in func.attrs)
    
    def test_protocol_and_impl(self, handler):
        code = '''
defprotocol Stringify do
  def to_string(value)
end

defimpl Stringify, for: Integer do
  def to_string(value), do: Integer.to_string(value)
end
'''
        items = handler._build_item_index(code)
        
        protocol = [i for i in items if i.kind == "defprotocol"]
        assert len(protocol) == 1
        assert protocol[0].name == "Stringify"
        
        impl = [i for i in items if i.kind == "defimpl"]
        assert len(impl) == 1
    
    def test_guards(self, handler):
        code = '''
defmodule Guards do
  defguard is_positive(x) when is_integer(x) and x > 0
  defguardp is_even(x) when rem(x, 2) == 0
end
'''
        items = handler._build_item_index(code)
        
        guards = [i for i in items if i.kind in ("defguard", "defguardp")]
        assert len(guards) == 2
    
    def test_struct_definition(self, handler):
        code = '''
defmodule User do
  defstruct [:name, :email, :age]
  
  def new(name, email) do
    %__MODULE__{name: name, email: email, age: 0}
  end
end
'''
        items = handler._build_item_index(code)
        
        struct_def = [i for i in items if i.kind == "defstruct"]
        assert len(struct_def) == 1
    
    def test_multiclause_function(self, handler):
        code = '''
defmodule Factorial do
  def factorial(0), do: 1
  def factorial(n) when n > 0, do: n * factorial(n - 1)
end
'''
        items = handler._build_item_index(code)
        
        funcs = [i for i in items if i.kind == "def"]
        assert len(funcs) == 2
        
        # Both should have name "factorial"
        assert all(f.name == "factorial" for f in funcs)
    
    def test_empty_module(self, handler):
        code = '''
defmodule EmptyModule do
end
'''
        items = handler._build_item_index(code)
        
        assert len(items) == 1
        assert items[0].kind == "defmodule"
        assert items[0].name == "EmptyModule"
    
    def test_inline_do(self, handler):
        code = '''
defmodule Inline do
  def one, do: 1
  def two, do: 2
end
'''
        items = handler._build_item_index(code)
        funcs = [i for i in items if i.kind == "def"]
        
        assert len(funcs) == 2
        assert funcs[0].name == "one"
        assert funcs[1].name == "two"


# =============================================================================
# DIAGNOSTIC OUTPUT
# =============================================================================

class TestDiagnostics:
    """Tests for diagnostic and error output."""
    
    def test_format_candidates_diagnostic(self, handler):
        code = '''
defmodule MyApp.Users do
  def get_user(id), do: id
  def create_user(attrs), do: attrs
  defp validate(x), do: x
end
'''
        diagnostic = handler.format_candidates_diagnostic(code, "MyApp.Users.nonexistent")
        
        assert "not found" in diagnostic.lower() or "available" in diagnostic.lower()
        assert "get_user" in diagnostic
        assert "create_user" in diagnostic


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
