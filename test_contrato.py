#!/usr/bin/env python3
"""Contrato entre core.py y la interfaz GTK (rolight.py).

rolight.py hace `import core` y usa sus helpers. core.py también es el script de
modo de la versión rofi, así que se toca seguido: este test falla si un cambio en
core.py rompe la versión GTK (sway/Hyprland) sin que nadie lo note.

    python test_contrato.py
"""
import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROLIGHT_PY = os.path.join(HERE, "rolight.py")


def symbols_used_from_core():
    """Todos los `core.<algo>` que rolight.py referencia."""
    with open(ROLIGHT_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read(), ROLIGHT_PY)
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "core":
            used.add(node.attr)
    return used


def core_definitions():
    """Símbolos públicos definidos en core.py (funciones, clases y constantes)."""
    with open(os.path.join(HERE, "core.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read(), "core.py")
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id.isupper():
            names.add(node.target.id)
    return names


class TestContratoCoreGtk(unittest.TestCase):
    def test_core_importa(self):
        sys.path.insert(0, HERE)
        import core  # noqa: F401

    def test_todo_lo_que_usa_rolight_py_existe(self):
        faltantes = symbols_used_from_core() - core_definitions()
        self.assertEqual(faltantes, set(),
                         f"rolight.py usa estos símbolos de core que ya no existen: "
                         f"{sorted(faltantes)}")

    def test_rolight_py_compila(self):
        with open(ROLIGHT_PY, encoding="utf-8") as f:
            compile(f.read(), ROLIGHT_PY, "exec")

    def test_core_compila(self):
        path = os.path.join(HERE, "core.py")
        with open(path, encoding="utf-8") as f:
            compile(f.read(), path, "exec")

    def test_find_hidden_opt_in(self):
        """fd --hidden cambia los resultados: tiene que seguir siendo opt-in,
        porque la versión GTK viene con el comportamiento de siempre."""
        import core
        os.environ.pop("ROLIGHT_FIND_HIDDEN", None)
        self.assertFalse(core.FIND_HIDDEN)


if __name__ == "__main__":
    unittest.main(verbosity=2)