"""
Grammar-driven parser for vibemod target paths.

Uses parsimonious PEG parser with a declarative grammar specification.
The grammar serves as both documentation and implementation.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from parsimonious.grammar import Grammar
from parsimonious.nodes import NodeVisitor

# =============================================================================
# GRAMMAR
# =============================================================================

GRAMMAR = Grammar(r"""
    target          = type_trait_method / type_trait / use_target / bare_use_path / module_target
    module_target   = module_path? main_target insertion_anchor?
    
    module_path     = (identifier "::")+
    
    main_target     = scoped_target / type_ns_struct / type_ns_impl_method / type_ns_impl / impl_method / impl_target / item_method / item_target
    
    scoped_target   = scope_base "." locator_expr
    scope_base      = type_ns_struct / type_ns_impl_method / type_ns_impl / impl_method / impl_target / item_method / item_target
    
    # Type namespace explicit targets
    type_ns_struct  = identifier ".struct"
    type_ns_impl    = identifier ".impl"
    type_ns_impl_method = identifier ".impl." method_name
    
    # Type::Trait syntax for trait impls (at top level to avoid module_path conflict)
    type_trait      = identifier "::" identifier !("." / "::")
    type_trait_method = identifier "::" identifier "." method_name
    
    # Use statement target: use:path or bare path::{...} or path::*
    use_target      = "use:" use_path 
    bare_use_path   = use_path_prefix "::" use_path_suffix 
    use_path_prefix = identifier ("::" identifier)*
    use_path_suffix = grouped_import / wildcard
    grouped_import  = "{" ~r"[^}]+" "}"
    wildcard        = "*"
    use_path        = ~r"[^\n@]+"
    
    # Legacy Type.method -> impl:Type.method
    item_method     = identifier "." method_name method_mods?
    
    impl_method     = impl_base "." method_name method_mods?
    impl_base       = impl_spec impl_mods?
    impl_mods       = attr_filter? occurrence?
    method_mods     = attr_filter? occurrence?
    
    impl_target     = impl_spec attr_filter? occurrence?
    impl_spec       = impl_colon / impl_paren
    impl_colon      = "impl:" type_spec
    impl_paren      = "impl(" type_spec ")"
    type_spec       = trait_impl / type_name
    trait_impl      = type_name " for " type_name
    type_name       = identifier
    method_name     = identifier
    
    item_target     = identifier attr_filter? occurrence?
    
    locator_expr    = "@" (insert_op / replace_op)
    insert_op       = insert_side (regex / statement)
    insert_side     = ("before" / "after") ":"
    replace_op      = regex
    
    regex           = "/" regex_body "/" occurrence?
    regex_body      = ~r"[^/]+"
    statement       = ~r".+"
    
    attr_filter     = "#[" attr_content "]"
    attr_content    = ~r"[^\]]+"
    
    occurrence      = "@" pos_int
    pos_int         = ~r"[0-9]+"
    
    insertion_anchor = "@" anchor_kind
    anchor_kind     = "append_file" / "append_module" / insert_ref
    insert_ref      = ("insert_before" / "insert_after") "(" identifier ")"
    
    identifier      = ~r"[a-zA-Z_][a-zA-Z0-9_]*"
""")


# =============================================================================
# AST
# =============================================================================

@dataclass
class TargetPath:
    """Structured representation of a parsed target path."""
    raw: str = ""
    module_path: List[str] = field(default_factory=list)
    item_name: Optional[str] = None
    impl_type: Optional[str] = None
    impl_trait: Optional[str] = None
    associated_name: Optional[str] = None
    attr_filter: Optional[str] = None
    occurrence: Optional[int] = None
    insertion_anchor: Optional[str] = None
    insertion_ref: Optional[str] = None
    # Scoped operations
    scope_path: Optional[str] = None
    anchor_type: Optional[str] = None  # "after", "before", or "replace"
    anchor_expr: Optional[str] = None
    anchor_is_regex: bool = False
    anchor_occurrence: Optional[int] = None
    # Type namespace targeting
    type_namespace: Optional[str] = None  # The type name when using namespace syntax
    ns_member: Optional[str] = None  # "struct", "impl", or trait name
    # Use statement targeting
    use_path: Optional[str] = None  # The path after "use:" e.g. "crate::{foo, bar}"

    @property
    def is_impl_target(self) -> bool:
        return self.impl_type is not None

    @property
    def is_trait_impl(self) -> bool:
        return self.impl_trait is not None

    @property
    def is_insertion(self) -> bool:
        return self.insertion_anchor is not None
    
    @property
    def is_scoped_insertion(self) -> bool:
        return self.scope_path is not None and self.anchor_type is not None
    
    @property
    def is_type_namespace(self) -> bool:
        return self.type_namespace is not None
    
    @property
    def is_use_target(self) -> bool:
        return self.use_path is not None
    
    @property
    def is_full_type_namespace(self) -> bool:
        """True if targeting the full type namespace (no .struct/.impl/.Trait)"""
        return self.type_namespace is not None and self.ns_member is None


# =============================================================================
# TREE WALKER (extracts data from parse tree)
# =============================================================================

def _find_node(node, expr_name):
    """Find first descendant node with given expression name."""
    if node.expr_name == expr_name:
        return node
    for child in node:
        result = _find_node(child, expr_name)
        if result:
            return result
    return None

def _find_all_nodes(node, expr_name):
    """Find all descendant nodes with given expression name."""
    results = []
    if node.expr_name == expr_name:
        results.append(node)
    for child in node:
        results.extend(_find_all_nodes(child, expr_name))
    return results

def _extract_from_tree(tree) -> TargetPath:
    """Extract TargetPath data from parse tree."""
    result = TargetPath(raw=tree.text)
    
    # Check for top-level type_trait patterns first
    type_trait_method = _find_node(tree, 'type_trait_method')
    if type_trait_method:
        identifiers = _find_all_nodes(type_trait_method, 'identifier')
        if len(identifiers) >= 3:
            result.type_namespace = identifiers[0].text
            result.ns_member = identifiers[1].text
            result.impl_type = identifiers[0].text
            result.impl_trait = identifiers[1].text
            result.associated_name = identifiers[2].text
        return result
    
    type_trait = _find_node(tree, 'type_trait')
    if type_trait:
        identifiers = _find_all_nodes(type_trait, 'identifier')
        if len(identifiers) >= 2:
            result.type_namespace = identifiers[0].text
            result.ns_member = identifiers[1].text
            result.impl_type = identifiers[0].text
            result.impl_trait = identifiers[1].text
        return result
    
    # Check for use targets (use:path or bare path::{...})
    use_target = _find_node(tree, 'use_target')
    if use_target:
        use_path = _find_node(use_target, 'use_path')
        if use_path:
            result.use_path = use_path.text
        return result
    
    bare_use_path = _find_node(tree, 'bare_use_path')
    if bare_use_path:
        # Reconstruct the full path from the parse tree
        result.use_path = bare_use_path.text
        return result
    
    # Module path (inside module_target)
    mod_node = _find_node(tree, 'module_path')
    if mod_node:
        result.module_path = mod_node.text.rstrip(':').split('::')
    
    # Check for scoped target first
    scoped = _find_node(tree, 'scoped_target')
    if scoped:
        # Extract the base (before locator)
        scope_base = _find_node(scoped, 'scope_base')
        if scope_base:
            _extract_scope_base(scope_base, result)
            result.scope_path = _build_scope_path(result)
        
        # Extract locator
        locator = _find_node(scoped, 'locator_expr')
        if locator:
            _extract_locator(locator, result)
        return result
    
    # Check various target types in order
    _extract_main_target(tree, result)
    
    # Insertion anchor
    anchor = _find_node(tree, 'insertion_anchor')
    if anchor:
        _extract_insertion_anchor(anchor, result)
    
    return result

def _extract_main_target(node, result):
    """Extract target info from main_target or scope_base."""
    # Type namespace explicit targets
    type_ns_struct = _find_node(node, 'type_ns_struct')
    if type_ns_struct:
        ident = _find_node(type_ns_struct, 'identifier')
        if ident:
            result.type_namespace = ident.text
            result.ns_member = 'struct'
        return
    
    type_ns_impl_method = _find_node(node, 'type_ns_impl_method')
    if type_ns_impl_method:
        identifiers = [child for child in type_ns_impl_method if child.expr_name == 'identifier']
        if identifiers:
            result.type_namespace = identifiers[0].text
            result.ns_member = 'impl'
            result.impl_type = identifiers[0].text
            if len(identifiers) >= 2:
                result.associated_name = identifiers[-1].text
        return
    
    type_ns_impl = _find_node(node, 'type_ns_impl')
    if type_ns_impl:
        ident = _find_node(type_ns_impl, 'identifier')
        if ident:
            result.type_namespace = ident.text
            result.ns_member = 'impl'
            result.impl_type = ident.text
        return
    
    # Use statement target: use:path
    use_target = _find_node(node, 'use_target')
    if use_target:
        use_path = _find_node(use_target, 'use_path')
        if use_path:
            result.use_path = use_path.text
        return
    
    # Type::Trait syntax
    type_trait_method = _find_node(node, 'type_trait_method')
    if type_trait_method:
        identifiers = _find_all_nodes(type_trait_method, 'identifier')
        if len(identifiers) >= 3:
            result.type_namespace = identifiers[0].text
            result.ns_member = identifiers[1].text
            result.impl_type = identifiers[0].text
            result.impl_trait = identifiers[1].text
            result.associated_name = identifiers[2].text
        return
    
    type_trait = _find_node(node, 'type_trait')
    if type_trait:
        identifiers = _find_all_nodes(type_trait, 'identifier')
        if len(identifiers) >= 2:
            result.type_namespace = identifiers[0].text
            result.ns_member = identifiers[1].text
            result.impl_type = identifiers[0].text
            result.impl_trait = identifiers[1].text
        return
    
    # Legacy Type.method -> impl:Type.method
    item_method = _find_node(node, 'item_method')
    if item_method:
        identifiers = [child for child in item_method if child.expr_name == 'identifier']
        if identifiers:
            result.impl_type = identifiers[0].text
            if len(identifiers) >= 2:
                result.associated_name = identifiers[-1].text
        return
    
    # impl:Type.method
    impl_method = _find_node(node, 'impl_method')
    if impl_method:
        _extract_impl_method(impl_method, result)
        return
    
    # impl:Type or impl(Type)
    impl_target = _find_node(node, 'impl_target')
    if impl_target:
        _extract_impl_target(impl_target, result)
        return
    
    # Simple item
    item_target = _find_node(node, 'item_target')
    if item_target:
        _extract_item_target(item_target, result)

def _extract_scope_base(node, result):
    """Extract target info from scope_base node."""
    _extract_main_target(node, result)

def _extract_impl_method(node, result):
    """Extract from impl_method: impl_base "." method_name method_mods?"""
    impl_base = _find_node(node, 'impl_base')
    if impl_base:
        _extract_impl_base(impl_base, result)
    
    # Method name is the identifier AFTER impl_base in impl_method
    for child in node:
        if child.expr_name == 'identifier':
            result.associated_name = child.text
            break

def _extract_impl_base(node, result):
    """Extract from impl_base: impl_spec impl_mods?"""
    impl_spec = _find_node(node, 'impl_spec')
    if impl_spec:
        _extract_impl_spec(impl_spec, result)
    
    # impl_mods: attr_filter? occurrence?
    impl_mods = _find_node(node, 'impl_mods')
    if impl_mods:
        attr = _find_node(impl_mods, 'attr_filter')
        if attr:
            result.attr_filter = attr.text
        occ = _find_node(impl_mods, 'occurrence')
        if occ:
            result.occurrence = int(_find_node(occ, 'pos_int').text)

def _extract_impl_target(node, result):
    """Extract from impl_target: impl_spec attr_filter? occurrence?"""
    impl_spec = _find_node(node, 'impl_spec')
    if impl_spec:
        _extract_impl_spec(impl_spec, result)
    
    attr = _find_node(node, 'attr_filter')
    if attr:
        result.attr_filter = attr.text
    
    occ = _find_node(node, 'occurrence')
    if occ:
        result.occurrence = int(_find_node(occ, 'pos_int').text)

def _extract_impl_spec(node, result):
    """Extract from impl_spec: impl_colon / impl_paren"""
    type_spec = _find_node(node, 'type_spec')
    if type_spec:
        trait_impl = _find_node(type_spec, 'trait_impl')
        if trait_impl:
            # trait_impl: type_name " for " type_name
            identifiers = _find_all_nodes(trait_impl, 'identifier')
            if len(identifiers) >= 2:
                result.impl_trait = identifiers[0].text
                result.impl_type = identifiers[1].text
        else:
            # Simple type name
            ident = _find_node(type_spec, 'identifier')
            if ident:
                result.impl_type = ident.text

def _extract_item_target(node, result):
    """Extract from item_target: identifier attr_filter? occurrence?"""
    ident = _find_node(node, 'identifier')
    if ident:
        result.item_name = ident.text
    
    attr = _find_node(node, 'attr_filter')
    if attr:
        result.attr_filter = attr.text
    
    occ = _find_node(node, 'occurrence')
    if occ:
        result.occurrence = int(_find_node(occ, 'pos_int').text)

def _extract_locator(node, result):
    """Extract from locator_expr: "@" (insert_op / replace_op)"""
    insert_side = _find_node(node, 'insert_side')
    
    if insert_side:
        # It's an insert_op (has before/after)
        result.anchor_type = insert_side.text.rstrip(':')
        
        regex = _find_node(node, 'regex')
        if regex:
            _extract_regex(regex, result)
        else:
            stmt = _find_node(node, 'statement')
            if stmt:
                result.anchor_expr = stmt.text
                result.anchor_is_regex = False
    else:
        # No insert_side means it's replace_op (just regex)
        result.anchor_type = "replace"
        regex = _find_node(node, 'regex')
        if regex:
            _extract_regex(regex, result)

def _extract_regex(node, result):
    """Extract from regex: "/" regex_body "/" occurrence?"""
    body = _find_node(node, 'regex_body')
    if body:
        result.anchor_expr = body.text
        result.anchor_is_regex = True
    
    occ = _find_node(node, 'occurrence')
    if occ:
        result.anchor_occurrence = int(_find_node(occ, 'pos_int').text)
    else:
        result.anchor_occurrence = 1  # Default

def _extract_insertion_anchor(node, result):
    """Extract from insertion_anchor: "@" anchor_kind"""
    text = node.text[1:]  # Skip the @
    
    if text == 'append_file':
        result.insertion_anchor = 'append_file'
    elif text == 'append_module':
        result.insertion_anchor = 'append_module'
    elif text.startswith('insert_before('):
        result.insertion_anchor = 'insert_before'
        result.insertion_ref = text[14:-1]
    elif text.startswith('insert_after('):
        result.insertion_anchor = 'insert_after'
        result.insertion_ref = text[13:-1]

def _build_scope_path(result):
    """Build scope path string from parsed components."""
    parts = []
    if result.module_path:
        parts.append('::'.join(result.module_path))
    
    if result.type_namespace:
        ns_str = result.type_namespace
        if result.ns_member:
            ns_str += f".{result.ns_member}"
        if result.associated_name:
            ns_str += f".{result.associated_name}"
        parts.append(ns_str)
    elif result.impl_type:
        if result.impl_trait:
            impl_str = f"impl:{result.impl_trait} for {result.impl_type}"
        else:
            impl_str = f"impl:{result.impl_type}"
        if result.occurrence:
            impl_str += f"@{result.occurrence}"
        if result.associated_name:
            impl_str += f".{result.associated_name}"
        parts.append(impl_str)
    elif result.item_name:
        item_str = result.item_name
        if result.occurrence:
            item_str += f"@{result.occurrence}"
        parts.append(item_str)
    
    return '::'.join(parts) if len(parts) > 1 else (parts[0] if parts else None)


# =============================================================================
# PUBLIC API
# =============================================================================

def parse_target_path(target_path: str) -> TargetPath:
    """
    Parse a vibemod target path string into structured form.
    
    Examples:
        "MyStruct" -> item_name="MyStruct"
        "impl:MyStruct.new" -> impl_type="MyStruct", associated_name="new"
        "MyStruct.struct" -> type_namespace="MyStruct", ns_member="struct"
        "MyStruct.impl" -> type_namespace="MyStruct", ns_member="impl"
        "MyStruct.Default" -> type_namespace="MyStruct", ns_member="Default" (trait impl)
        "MyStruct.impl.new" -> type_namespace="MyStruct", ns_member="impl", associated_name="new"
        "my_fn.@after:/pattern/@1" -> scope_path="my_fn", anchor_type="after", ...
    
    Raises:
        parsimonious.exceptions.ParseError: If the target path is invalid
    """
    tree = GRAMMAR.parse(target_path.strip())
    return _extract_from_tree(tree)


# =============================================================================
# CLI TEST
# =============================================================================

if __name__ == '__main__':
    test_cases = [
        # Basic targets
        "MyStruct",
        "foo::bar::MyStruct",
        "impl:MyStruct",
        "impl:MyStruct.new",
        "impl(MyStruct).new",
        "impl:Display for MyStruct.fmt",
        "impl:MyStruct@2.process",
        "impl:MyStruct#[cfg(test)].debug",
        # Type namespace targets
        "CodecConfig.struct",
        "CodecConfig.impl",
        "CodecConfig.impl.new",
        # Type::Trait syntax for trait impls
        "CodecConfig::Default",
        "CodecConfig::Default.default",
        # Legacy Type.method -> impl method
        "Calculator.add",
        "Calculator.subtract",
        # Use statement targets
        "use:std::collections::HashMap",
        "use:crate::{foo, bar, baz}",
        "use:crate::{higgs, KernelDigest, Multivector}",
        # Scoped targets
        "my_fn.@after:let x = 1;",
        "my_fn.@before:/assert!\\(/@1",
        r"my_fn.@/return result/@2",
        "impl:Kernel.apply.@after:/let spin =/@1",
        # Insertion anchors
        "MyStruct@append_file",
        "my_fn@insert_after(other_fn)",
    ]
    
    for tc in test_cases:
        try:
            result = parse_target_path(tc)
            print(f"✓ {tc}")
            if result.item_name:
                print(f"    item_name={result.item_name}")
            if result.type_namespace:
                print(f"    type_namespace={result.type_namespace}")
            if result.ns_member:
                print(f"    ns_member={result.ns_member}")
            if result.impl_type:
                print(f"    impl_type={result.impl_type}")
            if result.impl_trait:
                print(f"    impl_trait={result.impl_trait}")
            if result.associated_name:
                print(f"    associated_name={result.associated_name}")
            if result.use_path:
                print(f"    use_path={result.use_path}")
            if result.occurrence:
                print(f"    occurrence={result.occurrence}")
            if result.attr_filter:
                print(f"    attr_filter={result.attr_filter}")
            if result.scope_path:
                print(f"    scope_path={result.scope_path}")
                print(f"    anchor_type={result.anchor_type}")
                print(f"    anchor_expr={result.anchor_expr}")
                print(f"    anchor_is_regex={result.anchor_is_regex}")
                print(f"    anchor_occurrence={result.anchor_occurrence}")
            if result.insertion_anchor:
                print(f"    insertion_anchor={result.insertion_anchor}")
                print(f"    insertion_ref={result.insertion_ref}")
            print()
        except Exception as e:
            print(f"✗ {tc}")
            print(f"    Error: {e}")
            print()
