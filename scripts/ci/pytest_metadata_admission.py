"""Source-only pytest metadata admission (G2-R1/R2).

This is a metadata fence, not an import sandbox. Only values which pytest interprets
as markers/ParameterSets, collected decorators/bases, and collection configuration
are authority inputs. Ordinary tuple *values* do not become per-case marks. Unknown
metadata refuses; no source tuple, caller reason or path grants qualification.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TestAdmission:
    status: Literal["OFFLINE", "LIVE", "UNKNOWN"]
    reason: str
    source_sha256: str | None
    dependencies: tuple[tuple[str, str], ...]


class UnresolvedMetadataError(ValueError):
    pass


class LiveMetadataError(ValueError):
    pass


@dataclass
class Binding:
    node: ast.AST
    scope: Scope
    imported: str = ""


class Scope:
    def __init__(self, tree: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef, path: str):
        self.tree = tree
        self.path = path
        self.bindings: dict[str, Binding] = {}
        self.mutated: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import | ast.ImportFrom):
                for item in node.names:
                    name = item.asname or item.name.split(".")[0]
                    if isinstance(node, ast.Import):
                        imported = item.name if item.asname else item.name.split(".")[0]
                    else:
                        prefix = node.module or ""
                        if node.level:
                            package = path.removesuffix(".py").split("/")[:-1]
                            if package and package[0] == "src":
                                package = package[1:]
                            if node.level > len(package):
                                raise UnresolvedMetadataError("relative import escapes source package")
                            prefix = ".".join(
                                package[: len(package) - node.level + 1] + ([prefix] if prefix else [])
                            )
                        imported = prefix + "." + item.name
                    self._bind(name, Binding(node, self, imported))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                self._bind(node.name, Binding(node, self))
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and node.value is not None:
                        self._bind(target.id, Binding(node.value, self))
                    elif not isinstance(target, ast.Attribute) or target.attr in {
                        "marks",
                        "pytestmark",
                        "__dict__",
                        "__class__",
                    }:
                        self.mutated.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
                    else:
                        self.mutated.update(
                            n.id
                            for n in ast.walk(target)
                            if isinstance(n, ast.Name)
                            and n.id in self.bindings
                            and self.bindings[n.id].imported
                        )
            elif not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Constant):
                self.mutated.update(
                    n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
                )
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                    call = node.value
                    if isinstance(call.func, ast.Attribute):
                        self.mutated.update(
                            n.id for n in ast.walk(call.func.value) if isinstance(n, ast.Name)
                        )

    def _bind(self, name: str, binding: Binding) -> None:
        if name in self.bindings:
            self.mutated.add(name)
        self.bindings[name] = binding


# Anchored imported identities, never an unqualified constructor name. These
# constructors cannot produce pytest ParameterSet objects. Their bytes/version
# remain covered by the caller's source/lock provenance, not executed here.
_PLAIN_CONSTRUCTORS = {
    "builtins.bool",
    "builtins.bytes",
    "builtins.bytearray",
    "builtins.complex",
    "builtins.dict",
    "builtins.float",
    "builtins.frozenset",
    "builtins.int",
    "builtins.memoryview",
    "builtins.object",
    "builtins.set",
    "builtins.str",
    "builtins.TimeoutError",
    "builtins.ConnectionResetError",
    "builtins.EOFError",
    "builtins.OSError",
    "builtins.RuntimeError",
    "datetime.datetime",
    "datetime.timedelta",
    "ssl.SSLError",
    "ssl.SSLCertVerificationError",
    "types.SimpleNamespace",
    "httpcore.ReadError",
}
_PLAIN_DECORATORS = {"dataclasses.dataclass"}
_BUILTINS = {
    name.removeprefix("builtins.") for name in _PLAIN_CONSTRUCTORS if name.startswith("builtins.")
} | {"list", "tuple", "sorted", "range"}


class Classifier:
    def __init__(self, read: Callable[[str], bytes] | None = None):
        self.read = read
        self.modules: dict[str, Scope] = {}
        self.active: set[tuple[str, str, str]] = set()
        self.steps = 0
        self.external_checked: set[tuple[str, str]] = set()

    def parse(self, source: bytes, path: str) -> Scope:
        if path in self.modules:
            return self.modules[path]
        if len(self.modules) >= 80 or len(source) > 2_000_000:
            raise UnresolvedMetadataError("metadata source closure exceeds bound")
        scope = Scope(ast.parse(source), path)
        self.modules[path] = scope
        return scope

    def imported(self, name: str) -> Binding:
        if self.read is None:
            raise UnresolvedMetadataError("unbound imported metadata: " + name)
        parts = name.split(".")
        if not all(part.isidentifier() for part in parts):
            raise UnresolvedMetadataError("unsafe imported metadata name")
        for split in range(len(parts) - 1, 0, -1):
            module = "/".join(parts[:split])
            for path in (
                module + ".py",
                module + "/__init__.py",
                "src/" + module + ".py",
                "src/" + module + "/__init__.py",
            ):
                try:
                    source = self.read(path)
                except FileNotFoundError:
                    continue
                scope = self.parse(source, path)
                self.module_metadata(scope, collected=False)
                ancestors = path.split("/")[:-1]
                for count in range(1, len(ancestors) + 1):
                    parent = "/".join(ancestors[:count]) + "/__init__.py"
                    if parent == path:
                        continue
                    try:
                        package = self.read(parent)
                    except FileNotFoundError:
                        continue
                    self.module_metadata(self.parse(package, parent), collected=False)
                if split != len(parts) - 1:
                    raise UnresolvedMetadataError("unresolved imported attribute: " + name)
                return self.lookup(parts[-1], scope)
        raise UnresolvedMetadataError("unresolved imported metadata: " + name)

    def lookup(self, name: str, scope: Scope) -> Binding:
        if name in scope.mutated:
            raise UnresolvedMetadataError("rebound/mutated metadata dependency: " + name)
        if name not in scope.bindings:
            raise UnresolvedMetadataError("unresolved metadata name: " + name)
        return scope.bindings[name]

    def identity(self, node: ast.AST, scope: Scope, seen: frozenset[str] = frozenset()) -> str:
        if isinstance(node, ast.Name):
            if node.id in seen or node.id in scope.mutated:
                return ""
            binding = scope.bindings.get(node.id)
            if binding is None:
                return "builtins." + node.id if node.id in _BUILTINS else ""
            if binding.imported:
                self.external_identity(binding.imported, scope)
                return binding.imported
            if isinstance(binding.node, ast.Name | ast.Attribute):
                return self.identity(binding.node, binding.scope, seen | {node.id})
        if isinstance(node, ast.Attribute):
            prefix = self.identity(node.value, scope, seen)
            return prefix + "." + node.attr if prefix else ""
        return ""

    def external_identity(self, identity: str, scope: Scope) -> None:
        root = identity.split(".")[0]
        if (
            root
            not in {
                "pytest",
                "builtins",
                "json",
                "pathlib",
                "datetime",
                "ssl",
                "types",
                "dataclasses",
                "pydantic",
                "enum",
                "httpcore",
            }
            or self.read is None
        ):
            return
        key = (root, scope.path)
        if key in self.external_checked:
            return
        directory = scope.path.rsplit("/", 1)[0] if "/" in scope.path else ""
        for prefix in {"", "src/", directory + "/" if directory else ""}:
            for suffix in (".py", "/__init__.py"):
                try:
                    self.read(prefix + root + suffix)
                except FileNotFoundError:
                    continue
                raise UnresolvedMetadataError("local shadow of trusted metadata dependency: " + root)
        self.external_checked.add(key)

    def path_value(self, node: ast.AST, scope: Scope) -> bool:
        if isinstance(node, ast.Call) and self.identity(node.func, scope) == "pathlib.Path":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            return self.path_value(node.value, scope)
        if isinstance(node, ast.Name):
            binding = self.lookup(node.id, scope)
            return self.path_value(binding.node, binding.scope)
        return False

    def resolve(self, node: ast.AST, scope: Scope, role: str) -> tuple[ast.AST, Scope]:
        self.steps += 1
        if self.steps > 15000:
            raise UnresolvedMetadataError("metadata dependency recursion exceeds bound")
        key = (scope.path, ast.dump(node), role)
        if key in self.active:
            raise UnresolvedMetadataError("cyclic metadata dependency")
        if isinstance(node, ast.Name):
            binding = self.lookup(node.id, scope)
        elif isinstance(node, ast.Attribute):
            identity = self.identity(node, scope)
            if not identity:
                raise UnresolvedMetadataError("unresolved metadata attribute")
            binding = self.imported(identity)
        else:
            return node, scope
        self.active.add(key)
        try:
            imports: set[str] = set()
            while binding.imported:
                if binding.imported in imports:
                    raise UnresolvedMetadataError("cyclic imported metadata")
                imports.add(binding.imported)
                binding = self.imported(binding.imported)
            return self.resolve(binding.node, binding.scope, role)
        finally:
            self.active.remove(key)

    def marker(self, node: ast.AST, scope: Scope) -> None:
        if isinstance(node, ast.List | ast.Tuple):
            for item in node.elts:
                self.marker(item, scope)
            return
        target = node.func if isinstance(node, ast.Call) else node
        name = self.identity(target, scope)
        if name.startswith("pytest.mark.") and name.count(".") == 2:
            mark = name.rsplit(".", 1)[-1]
            if mark == "integration":
                raise LiveMetadataError("integration-marked source")
            if mark.startswith("_"):
                raise UnresolvedMetadataError("reflective/private MarkGenerator access")
            return
        if isinstance(node, ast.Name):
            value, context = self.resolve(node, scope, "mark")
            self.marker(value, context)
            return
        raise UnresolvedMetadataError("unresolved/dynamic marker metadata")

    def plain(self, node: ast.AST, scope: Scope) -> None:
        # Literal Python containers cannot be pytest ParameterSet instances. Their
        # contents are test values, not per-case metadata at this position.
        if isinstance(
            node, ast.Constant | ast.List | ast.Tuple | ast.Dict | ast.Set | ast.Lambda | ast.JoinedStr
        ):
            return
        if isinstance(node, ast.UnaryOp | ast.BinOp):
            self.primitive(node, scope)
            return
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if self.identity(target, scope) not in _PLAIN_DECORATORS:
                    raise UnresolvedMetadataError("decorated parameter object")
            if isinstance(node, ast.ClassDef):
                if node.keywords:
                    raise UnresolvedMetadataError("parameter class metaclass")
                for base in node.bases:
                    if self.identity(base, scope) not in {
                        "builtins.object",
                        "builtins.str",
                        "builtins.bytes",
                        "pydantic.BaseModel",
                        "enum.Enum",
                        "enum.IntEnum",
                        "enum.StrEnum",
                    }:
                        value, context = self.resolve(base, scope, "class base")
                        if not isinstance(value, ast.ClassDef):
                            raise UnresolvedMetadataError("unresolved parameter class base")
                        self.plain(value, context)
            return
        if isinstance(node, ast.Call):
            identity = self.identity(node.func, scope)
            if identity in _PLAIN_CONSTRUCTORS:
                return
            if identity == "pytest.param":
                for keyword in node.keywords:
                    if keyword.arg == "marks":
                        self.marker(keyword.value, scope)
                    elif keyword.arg is None:
                        raise UnresolvedMetadataError("dynamic pytest.param keyword metadata")
                return
            value, context = self.resolve(node.func, scope, "constructor")
            if isinstance(value, ast.ClassDef):
                self.plain(value, context)
                if value.bases or any(
                    isinstance(n, ast.FunctionDef) and n.name == "__new__" for n in value.body
                ):
                    raise UnresolvedMetadataError("parameter constructor inheritance or __new__")
                return
            if isinstance(value, ast.FunctionDef | ast.AsyncFunctionDef):
                self.function(value, context, node, scope, "plain")
                return
        if isinstance(node, ast.Name | ast.Attribute):
            value, context = self.resolve(node, scope, "plain")
            self.plain(value, context)
            return
        raise UnresolvedMetadataError("unresolved parameter value: " + ast.unparse(node)[:160])

    def primitive(self, node: ast.AST, scope: Scope) -> str:
        """Prove an exact builtin result shape, without dispatching any operator.

        A plain class instance or function result can overload arithmetic to return
        a ParameterSet. Only closed builtin operand shapes justify this shortcut.
        Container contents remain ordinary row values, not parameter metadata.
        """
        if isinstance(node, ast.Constant):
            return type(node.value).__name__
        if isinstance(node, ast.List | ast.Tuple | ast.Dict | ast.Set):
            return type(node).__name__.lower()
        if isinstance(node, ast.JoinedStr):
            for formatted in node.values:
                if isinstance(formatted, ast.FormattedValue):
                    self.primitive(formatted.value, scope)
            return "str"
        if isinstance(node, ast.Name | ast.Attribute):
            value, context = self.resolve(node, scope, "primitive operator operand")
            return self.primitive(value, context)
        if isinstance(node, ast.Call):
            identity = self.identity(node.func, scope)
            if identity in {
                "builtins.bool",
                "builtins.int",
                "builtins.float",
                "builtins.complex",
                "builtins.list",
                "builtins.tuple",
            }:
                return identity.removeprefix("builtins.")
            # Retain imported constructor provenance even when its instances
            # cannot establish a primitive operator result.
            self.resolve(node.func, scope, "operator constructor")
        numeric = {"bool", "int", "float", "complex", "number"}
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return "bool"  # __bool__ cannot override the result type of `not`.
            operand = self.primitive(node.operand, scope)
            if operand in numeric and isinstance(node.op, ast.UAdd | ast.USub | ast.Invert):
                return "int" if operand in {"bool", "int"} else operand
        if isinstance(node, ast.BinOp):
            left = self.primitive(node.left, scope)
            right = self.primitive(node.right, scope)
            if left in numeric and right in numeric and not isinstance(node.op, ast.MatMult):
                if (
                    left in {"bool", "int"}
                    and right in {"bool", "int"}
                    and not isinstance(node.op, ast.Div | ast.Pow)
                ):
                    return "int"
                return "number"
            if isinstance(node.op, ast.Add) and left == right and left in {"str", "bytes", "list", "tuple"}:
                return left
            if isinstance(node.op, ast.Mult):
                if left in {"str", "bytes", "list", "tuple"} and right in {"bool", "int"}:
                    return left
                if right in {"str", "bytes", "list", "tuple"} and left in {"bool", "int"}:
                    return right
        raise UnresolvedMetadataError("operator may dispatch user-defined parameter metadata")

    def parameters(self, node: ast.AST, scope: Scope) -> None:
        if isinstance(node, ast.List | ast.Tuple | ast.Set):
            for item in node.elts:
                self.plain(item, scope)
            return
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if key is None:
                    raise UnresolvedMetadataError("dynamic parameter dictionary expansion")
                self.plain(key, scope)
            return
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            self.parameters(node.left, scope)
            self.parameters(node.right, scope)
            return
        if isinstance(node, ast.ListComp | ast.GeneratorExp | ast.SetComp):
            # The emitted row's shape controls per-case marks, not iterator data
            # inside an ordinary tuple. Other shapes retain their dependencies.
            if (
                isinstance(node.elt, ast.Attribute)
                and node.elt.attr == "name"
                and isinstance(node.elt.value, ast.Name)
            ):
                for generator in node.generators:
                    if (
                        isinstance(generator.target, ast.Name)
                        and generator.target.id == node.elt.value.id
                        and isinstance(generator.iter, ast.Call)
                        and isinstance(generator.iter.func, ast.Attribute)
                        and generator.iter.func.attr == "iterdir"
                        and self.path_value(generator.iter.func.value, scope)
                    ):
                        return  # pathlib.Path.name is a string, never a ParameterSet.
            if isinstance(node.elt, ast.Name):
                for generator in node.generators:
                    if isinstance(generator.target, ast.Name) and generator.target.id == node.elt.id:
                        self.parameters(generator.iter, scope)
                        return
            self.plain(node.elt, scope)
            return
        if isinstance(node, ast.Call):
            identity = self.identity(node.func, scope)
            if identity == "builtins.range":
                return
            if identity in {"builtins.list", "builtins.tuple", "builtins.sorted"} and len(node.args) == 1:
                self.parameters(node.args[0], scope)
                return
            if identity == "json.loads" and not node.keywords:
                # JSON's default grammar cannot encode a Python ParameterSet.
                return
            value, context = self.resolve(node.func, scope, "parameters")
            if isinstance(value, ast.FunctionDef | ast.AsyncFunctionDef):
                self.function(value, context, node, scope, "parameters")
                return
        if isinstance(node, ast.Name | ast.Attribute):
            value, context = self.resolve(node, scope, "parameters")
            self.parameters(value, context)
            return
        raise UnresolvedMetadataError("unresolved parameter collection: " + ast.unparse(node)[:160])

    def function(
        self,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        owner: Scope,
        call: ast.Call,
        caller: Scope,
        role: str,
    ) -> None:
        if function.decorator_list:
            raise UnresolvedMetadataError("decorated parameter producer")
        local = Scope(function, owner.path)
        local.bindings = owner.bindings | local.bindings
        arguments = function.args.posonlyargs + function.args.args
        defaults = [None] * (len(arguments) - len(function.args.defaults)) + list(function.args.defaults)
        for argument, default in zip(arguments, defaults, strict=True):
            if default is not None:
                local.bindings[argument.arg] = Binding(default, owner)
        for argument, default in zip(function.args.kwonlyargs, function.args.kw_defaults, strict=True):
            if default is not None:
                local.bindings[argument.arg] = Binding(default, owner)
        for argument, value in zip(arguments, call.args, strict=False):
            local.bindings[argument.arg] = Binding(value, caller)
        for keyword in call.keywords:
            if keyword.arg is None:
                raise UnresolvedMetadataError("dynamic producer arguments")
            local.bindings[keyword.arg] = Binding(keyword.value, caller)
        returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
        if not returns or any(
            isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and n is not function
            for n in ast.walk(function)
        ):
            raise UnresolvedMetadataError("unresolved parameter producer returns")
        for node in returns:
            if node.value is None:
                raise UnresolvedMetadataError("empty parameter producer return")
            getattr(self, role)(node.value, local)

    def decorator(self, node: ast.AST, scope: Scope) -> None:
        target = node.func if isinstance(node, ast.Call) else node
        identity = self.identity(target, scope)
        if self.fixture_decorator(node, scope):
            return
        self.marker(node, scope)
        if identity == "pytest.mark.parametrize":
            if not isinstance(node, ast.Call):
                raise UnresolvedMetadataError("parametrize without bound values")
            values = (
                node.args[1]
                if len(node.args) >= 2
                else next((k.value for k in node.keywords if k.arg == "argvalues"), None)
            )
            if values is None or any(k.arg is None for k in node.keywords):
                raise UnresolvedMetadataError("unbound parametrize metadata")
            self.parameters(values, scope)

    def fixture_decorator(self, node: ast.AST, scope: Scope) -> bool:
        target = node.func if isinstance(node, ast.Call) else node
        identity = self.identity(target, scope)
        if identity not in {"pytest.fixture", "pytest.yield_fixture"}:
            # A named local fixture decorator may bind a configured factory call.
            if isinstance(node, ast.Name) and node.id in scope.bindings:
                binding = scope.bindings[node.id]
                if not binding.imported and isinstance(binding.node, ast.Name | ast.Call):
                    value, context = self.resolve(node, scope, "fixture decorator")
                    if isinstance(value, ast.Call):
                        return self.fixture_decorator(value, context)
            return False
        if not isinstance(node, ast.Call):
            return True
        # Pinned pytest fixture/yield_fixture accept only fixture_function as a
        # positional argument; params is keyword-only. None is the factory form.
        if len(node.args) > 1 or any(
            not isinstance(arg, ast.Constant) or arg.value is not None for arg in node.args
        ):
            raise UnresolvedMetadataError("unresolved positional fixture metadata")
        options: dict[str, Binding] = {}

        def add(name: str, value: ast.AST, context: Scope) -> None:
            if name in options or name not in {
                "fixture_function",
                "scope",
                "params",
                "autouse",
                "ids",
                "name",
            }:
                raise UnresolvedMetadataError("unresolved or duplicate fixture option")
            options[name] = Binding(value, context)

        def expand(value: ast.AST, context: Scope) -> None:
            value, context = self.resolve(value, context, "fixture keyword expansion")
            if not isinstance(value, ast.Dict):
                raise UnresolvedMetadataError("unresolved fixture keyword expansion")
            for key, item in zip(value.keys, value.values, strict=True):
                if key is None:
                    expand(item, context)
                elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                    add(key.value, item, context)
                else:
                    raise UnresolvedMetadataError("dynamic fixture option name")

        for keyword in node.keywords:
            if keyword.arg is None:
                expand(keyword.value, scope)
            else:
                add(keyword.arg, keyword.value, scope)
        if "fixture_function" in options:
            value, context = self.resolve(
                options["fixture_function"].node, options["fixture_function"].scope, "fixture function"
            )
            if node.args or not isinstance(value, ast.Constant) or value.value is not None:
                raise UnresolvedMetadataError("unresolved fixture function argument")
        if "params" in options:
            params = options["params"]
            value, context = self.resolve(params.node, params.scope, "fixture parameters")
            if not isinstance(value, ast.Constant) or value.value is not None:
                self.parameters(value, context)
        return True

    def module_metadata(self, scope: Scope, *, collected: bool = True) -> None:
        # Direct integration always wins, including aliased and inherited sources.
        if any(
            isinstance(node, ast.Attribute) and node.attr == "integration" for node in ast.walk(scope.tree)
        ):
            raise LiveMetadataError("integration-marked source")
        for node in scope.tree.body:
            if isinstance(node, ast.ImportFrom) and any(
                item.name == "*" or (item.asname or item.name) in {"pytestmark", "pytest_plugins"}
                for item in node.names
            ):
                raise UnresolvedMetadataError("imported collector/module metadata")
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (
                node.name.startswith("pytest_") or node.name in {"__getattr__", "__getattribute__", "__dir__"}
            ):
                raise UnresolvedMetadataError("collector hook: " + node.name)
            if isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {
                    item.id for target in targets for item in ast.walk(target) if isinstance(item, ast.Name)
                }
                if collected and any(name.startswith(("test_", "Test")) for name in names):
                    raise UnresolvedMetadataError("assigned collected object metadata")
                if "pytest_plugins" in names:
                    raise UnresolvedMetadataError("unresolved pytest plugin declaration")
                if "pytestmark" in names:
                    if "pytestmark" in scope.mutated or node.value is None:
                        raise UnresolvedMetadataError("mutated module markers")
                    self.marker(node.value, scope)
                if any(
                    isinstance(target, ast.Attribute)
                    and (
                        target.attr == "pytestmark" or self.identity(target.value, scope).startswith("pytest")
                    )
                    for target in targets
                ):
                    raise UnresolvedMetadataError("assigned pytest metadata attribute")
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                identity = self.identity(node.value.func, scope)
                if identity.startswith(("pytest", "sys.path", "sys.modules")) or not isinstance(
                    node.value.func, ast.Attribute
                ):
                    raise UnresolvedMetadataError("unresolved module call may mutate metadata")
            if isinstance(node, ast.If | ast.For | ast.While | ast.Try | ast.With) and any(
                (isinstance(n, ast.Name) and (n.id.startswith("pytest") or n.id.startswith("test_")))
                or (isinstance(n, ast.Attribute) and n.attr in {"mark", "pytestmark"})
                for n in ast.walk(node)
            ):
                raise UnresolvedMetadataError("conditional collection metadata")
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and any(self.fixture_decorator(d, scope) for d in node.decorator_list)
                and any(
                    isinstance(n, ast.Attribute) and n.attr in {"add_marker", "applymarker", "pytestmark"}
                    for statement in node.body
                    for n in ast.walk(statement)
                )
            ):
                raise UnresolvedMetadataError("fixture mutates collected metadata")
            if (
                collected
                and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name.startswith("test_")
            ):
                for decorator in node.decorator_list:
                    self.decorator(decorator, scope)
                if any(isinstance(item, ast.Call) for item in ast.walk(node.args)):
                    raise UnresolvedMetadataError("dynamic collected test defaults")
            if collected and isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                if node.bases or node.keywords:
                    raise UnresolvedMetadataError("inherited or metaclass collection metadata")
                for decorator in node.decorator_list:
                    self.decorator(decorator, scope)
                child = Scope(ast.Module(body=node.body, type_ignores=[]), scope.path)
                child.bindings = scope.bindings | child.bindings
                self.module_metadata(child)


def source_refusal(
    source: bytes, *, path: str = "test_source.py", read: Callable[[str], bytes] | None = None
) -> str | None:
    try:
        classifier = Classifier(read)
        classifier.module_metadata(classifier.parse(source, path))
    except LiveMetadataError:
        return "integration-marked source"
    except (UnresolvedMetadataError, SyntaxError, ValueError, UnicodeError, RecursionError) as exc:
        return "unknown/dynamic source classification: " + str(exc)
    return None
