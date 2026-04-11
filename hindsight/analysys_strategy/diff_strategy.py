from enum import Enum


class DiffStrategy(Enum):
    """
    Enum defining different strategies for analyzing code differences.
    """
    RECENTLY_MODIFIED_FILES = "recently_modified_files"
    BRANCH_BASED = "branch_based"
    ENTIRE_REPO = "entire_repo"
    AUTO = "auto"
