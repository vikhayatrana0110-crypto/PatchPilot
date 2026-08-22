import os
import logging
from charset_normalizer import from_path
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

DEPENDENCY_FILES=[
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "environment.yml",
    "Pipfile",
    "setup.py",
    "setup.cfg"
]

@tool
def inspect_dependencies(repository_path: str) -> str:
    """
    Inspects common dependency files (like requirements.txt or pyproject.toml) 
    in the repository and returns their contents.
    
    Args:
        repository_path: The absolute path to the root of the repository.
        
    Returns:
        A string containing the contents of any discovered dependency files.
    """
    logger.info("AGENT INVOKED - inspect_dependencies for repo path: '%s'",repository_path)

    repo_path = os.path.abspath(repository_path)

    if not os.path.isdir(repository_path):
        return f"error: the repo path '{repo_path}' does not exist."

    found_files=[]
    output=[]

    for filename in DEPENDENCY_FILES:
        filepath = os.path.join(repo_path,filename)
        if os.path.isfile(filepath):
            found_files.append(filename)
            try:
                #safety detect encoding to prevent corruption
                result = from_path(filepath).best()
                if result is None:
                    output.append(f"{filename} \nERROR: could not detect encoding\n")
                    continue

                content = str(result)
                #truncate if the file is big to prevent context overflow in LLM

                if len(content) > 10000:
                    content = content[:10000] + "\n (truncated due to size)"


                output.append(f"{filename}\n {content}\n")
            except Exception as e:
                logger.warning(
                    "failed to read dependecy file %s: %s",
                    filename,
                    e,exc_info=True
                    )
                output.append(f"{filename}\n  error reading file:{str(e)}\n")
    if not found_files:
        return f"No standard dependency files found in '{repo_path}'. Checked for: {', '.join(DEPENDENCY_FILES)}"
    return "\n".join(output)          