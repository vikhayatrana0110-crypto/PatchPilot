from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class DebuggingState(TypedDict):
    # input set once at start of debugging session
    repository_id: str #eg my_repo
    relative_file_path: str #eg src/my_file.py
    issue_description: str #user description of the issue to debug

    # tool outputs set during debugging session
    parsed_stack_trace: str #structured output from analyze_stack_trace tool
    retrieved_context: str #relevant code chunks
    dependencies: str #dependencies of the file being debugged

    # LLM output
    error_classification: str
    root_cause_analysis: str
    debug_plan: str
    target_file: str
    patch_type: str              # Add this! ('full' or 'snippet')
    generated_patch: str

    # validation results
    syntax_check_result: str #result of syntax check after applying patch
    lint_result: str #result of linting after applying patch
    test_result: str #result of running the target file's unit tests while patched

    # retry control
    patch_attempts: int #how many times generate_patch has run this session

    # human_in_the_loop
    human_decision: str #"approve" | "reject" | "revise" -- revise sends the feedback back to generate_patch
    human_approved: bool #whether the human approves the patch #true =apply patch, false = discard patch
    human_feedback: str

    # final
    final_report: str #final report of the debugging session

    # LLM message history
    messages: Annotated[list[BaseMessage], add_messages]