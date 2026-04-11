#!/usr/bin/env python3

# Copyright © 2024 Apple, Inc. All rights reserved.
#
# This document is the property of Apple, Inc.
# It is considered confidential and proprietary.
#
# This document may not be reproduced or transmitted in any form,
# in whole or in part, without the express written permission of
# Apple, Inc.


# To run: `python3 create_radar.py` or simply `./create_radar.py`.
#
# To install dependencies:
# ```
# python3 -m pip install --user -i https://pypi.apple.com/simple radarclient
# python3 -m pip install --user gitpython
# ```

# Pull Request without an issue in its title will not be merged to `main`.
# Several systems in Apple (HyperLoop, Iris, ATP, etc.) mandate this specification
# for projects that feed to the OS.

# This convenience script can help to create an issue with sensible defaults from
# the top-most commit in your local repo. If an issue is not found in top commit's
# one-line message, this script will create an issue for this change with reasonable
# defaults and print the issue id. Ensure you attach the issue's url to the PR's title.

import argparse
import radarclient
from radarclient import AppleDirectoryQuery, Category
import git
import os
from pathlib import Path

SCRIPT_DIR = Path(os.path.dirname(os.path.realpath(__file__)))
REPO_ROOT = SCRIPT_DIR.parent

ISSUE_URL_PREFIX = "rdar://"


def create_issue(title: str, description: str, user_dsid: int):
    print("Creating a new issue with default settings...")

    system_identifier = radarclient.ClientSystemIdentifier("OdieIssueClient", "1.0")
    radar_client = radarclient.RadarClient(
        radarclient.AuthenticationStrategySPNego(), system_identifier
    )

    # Obtained by running
    # radar_client.find_components({ 'name' : 'On-Device Inference Engine (ODIE)' })[0].id)
    odie_component_id = 1568098

    new_issue_data = {
        "title": title,
        "componentID": odie_component_id,
        "description": description,
        "classification": "Other Bug",
        "reproducible": "Not Applicable",
        "assigneeID": user_dsid,
    }

    issue = radar_client.create_radar(new_issue_data)
    issue.priority = 2
    issue.state = "Analyze"
    issue.substate = "Review"
    issue.milestone = {"name": "Future macOS Release"}
    issue.resolution = "Software Changed"
    issue.category = Category({"name": "Non-Tentpole Feature Work"})
    issue.commit_changes()

    print(f"New issue created with URL: {ISSUE_URL_PREFIX}{issue.id}")
    return issue.id


def get_dsid():
    """Returns DSID for user logged in AppleConnect or None on failure."""

    user_dsid = None
    try:
        ac_username = AppleDirectoryQuery.logged_in_appleconnect_accounts()[0].username
        user_dsid = AppleDirectoryQuery.user_entry_for_appleconnect_username(
            ac_username
        ).dsid()
    except Exception:
        pass
    return user_dsid


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Script to generate issue for local commit and update the commit with issue details."
    )
    parser.add_argument(
        "-dsid",
        "--user_dsid",
        type=int,
        help="User dsid to use, if not specified will use AC to look the DSID.",
        default=get_dsid(),
    )

    args = parser.parse_args()

    repo = git.Repo(str(REPO_ROOT))
    commit = repo.head.commit
    commit_message = commit.message
    original_commit_message = commit_message
    one_line_message = commit.message.split("\n", 1)[0]

    if ISSUE_URL_PREFIX not in one_line_message:
        issue_id = create_issue(
            title=one_line_message, description=commit_message, user_dsid=args.user_dsid
        )
        # Add issue url as title of commit message.
        commit_message = f"{ISSUE_URL_PREFIX}{issue_id} {commit_message}"

    if "Testing: " not in commit_message:
        print('\n"Testing: " not found in commit message. Need to add it.\n')
        testing = input("Enter the testing done for this PR -->  ")
        # Add Testing tag as last line of commit message.
        commit_message = f"{commit_message}\nTesting: {testing}"

    if commit_message != original_commit_message:
        repo.git.commit("--amend", "-m", f"{commit_message}")
        print("Amended commit message to include details about the issue.")

    print("Done.")
