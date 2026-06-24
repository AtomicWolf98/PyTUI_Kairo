import unittest
from tools.base import ToolRegistry, skill, BaseTool

class TestTools(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()

    def test_basic_tool_registration(self):
        class SimpleTool(BaseTool):
            name = "simple_test"
            description = "A simple tool for testing."
            parameters = {
                "type": "object",
                "properties": {
                    "val": {"type": "string"}
                },
                "required": ["val"]
            }
            def execute(self, val: str) -> str:
                return f"simple_{val}"

        tool = SimpleTool()
        self.registry.register(tool)
        self.assertIn("simple_test", self.registry.tools)
        
        # Test schema generation
        schemas = self.registry.get_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["function"]["name"], "simple_test")

        # Test execution
        res = self.registry.execute_tool("simple_test", '{"val": "hello"}')
        self.assertEqual(res, "simple_hello")

    def test_skill_decorator(self):
        # Declare a skill function
        @skill(name="decorated_test", description="Test decorated function.")
        def my_test_func(a: int, b: str) -> str:
            return f"res_{a}_{b}"

        # Get instance from function metadata
        tool_class = getattr(my_test_func, "_tool_class")
        tool = tool_class()
        self.registry.register(tool)

        self.assertIn("decorated_test", self.registry.tools)
        self.assertEqual(tool.description, "Test decorated function.")
        
        # Verify schema
        schemas = self.registry.get_schemas()
        properties = schemas[0]["function"]["parameters"]["properties"]
        self.assertIn("a", properties)
        self.assertIn("b", properties)
        self.assertEqual(properties["a"]["type"], "integer")
        self.assertEqual(properties["b"]["type"], "string")

        # Execute
        res = self.registry.execute_tool("decorated_test", '{"a": 42, "b": "ok"}')
        self.assertEqual(res, "res_42_ok")

if __name__ == "__main__":
    unittest.main()
