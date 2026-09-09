from livecodebench.prompts import SYSTEM_MESSAGE, user_prompt


def test_official_system_prompt():
    assert SYSTEM_MESSAGE == (
        "You are an expert Python programmer. You will be given a question (problem specification) "
        "and will generate a correct Python program that matches the specification and passes all tests."
    )


def test_official_stdin_template():
    assert user_prompt("Synthetic statement.") == (
        "### Question:\nSynthetic statement.\n\n"
        "### Format: Read the inputs from stdin solve the problem and write the answer to stdout "
        "(do not directly test on the sample inputs). Enclose your code within delimiters as follows. "
        "Ensure that when the python program runs, it reads the inputs, runs the algorithm and writes output to STDOUT.\n"
        "```python\n# YOUR CODE HERE\n```\n\n"
        "### Answer: (use the provided format with backticks)\n\n"
    )


def test_official_functional_template():
    assert user_prompt("Synthetic statement.", "class Solution:\n    pass") == (
        "### Question:\nSynthetic statement.\n\n"
        "### Format: You will use the following starter code to write the solution to the problem "
        "and enclose your code within delimiters.\n"
        "```python\nclass Solution:\n    pass\n```\n\n"
        "### Answer: (use the provided format with backticks)\n\n"
    )
