import unittest
import os
import textwrap
from src.vibemod.modify_code import modify_declaration

class TestDecoratorInsertion(unittest.TestCase):
    def setUp(self):
        self.test_file = "temp_decorated_test.py"
        # Create a file with imports and a decorated class
        with open(self.test_file, "w") as f:
            f.write(textwrap.dedent("""
                import dataclasses

                @dataclasses.dataclass
                class StructuralTypeScorer:
                    window: int = 1
            """))

    def tearDown(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

    def test_insert_before_decorator(self):
        """
        Verify that a new function is inserted BEFORE the @dataclass decorator.
        """
        # The new function to insert
        new_func_code = textwrap.dedent("""
            def temperature_schedule(t: int) -> float:
                return float(t)
        """)

        # Apply the modification
        # internal logic uses AST to find the first declaration and insert before it
        modify_declaration(
            file_path=self.test_file,
            dotted_target="temperature_schedule",
            content=new_func_code,
            remove=False
        )

        # Read the result
        with open(self.test_file, "r") as f:
            content = f.read()

        # Check the order of elements
        decorator_index = content.find("@dataclasses.dataclass")
        function_index = content.find("def temperature_schedule")

        # Assertions
        self.assertNotEqual(function_index, -1, "New function was not inserted.")
        self.assertNotEqual(decorator_index, -1, "Decorator was lost.")
        
        # The Critical Check: Function must appear BEFORE the decorator
        self.assertLess(
            function_index, 
            decorator_index, 
            f"Function was inserted after the decorator.\nFile Content:\n{content}"
        )

if __name__ == "__main__":
    unittest.main()