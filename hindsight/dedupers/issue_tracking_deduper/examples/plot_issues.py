#!/usr/bin/env python3

# Copyright © 2024 Apple, Inc. All rights reserved.
#
# This document is the property of Apple, Inc.
# It is considered confidential and proprietary.
#
# This document may not be reproduced or transmitted in any form,
# in whole or in part, without the express written permission of
# Apple, Inc.


# To run: `python3 plot_issues.py <issue-id> --output /path/to/output --exclude <regex to exclude>`
#
# To install dependencies:
# ```
# /usr/bin/curl -Lg 'https://artifacts.apple.com/sdp/g/liv/liv-[RELEASE].macos' -o /tmp/liv && chmod +x /tmp/liv
# /tmp/liv brew install
# brew install graphviz
# python3 -m pip install --user -i https://pypi.apple.com/simple radarclient
# python3 -m pip install --user pydot
# ```

import argparse
import re
from typing import Optional

import pydot
import radarclient


def is_closed(issue: radarclient.Radar) -> bool:
    return issue.state.lower() in {"verify", "closed"}


def node_for_issue(issue: radarclient.Radar) -> pydot.Node:
    return pydot.Node(
        f"{issue.id}",
        label=f"rdar://{issue.id}: {issue.title}",
        color="green" if is_closed(issue) else "black",
    )


class DownwardTraversalDelegate:
    def __init__(
        self, exclude_regex: Optional[re.Pattern], ignore_closed: bool = False
    ):
        self.seen = set()
        self.exclude_regex = exclude_regex
        self.ignore_closed = ignore_closed

    def should_follow_relationship(
        self, relationship: radarclient.Relationship
    ) -> bool:
        inverse = relationship.inverse_relationship()
        if (relationship in self.seen) or (inverse in self.seen):
            return False

        self.seen.update([relationship, inverse])

        # The exclude filter - if the title matches something we want to exclude, then don't include it.
        if self.exclude_regex and self.exclude_regex.search(
            relationship.related_radar.title
        ):
            return False

        # If the issue is marked as closed, then don't follow the relationship further.
        if self.ignore_closed and is_closed(relationship.radar):
            return False

        return relationship.type != radarclient.Relationship.TYPE_SUBTASK_OF


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Script to produce a graph of issues beginning from a query."
    )
    parser.add_argument(
        "query_id",
        type=int,
        help="Issue ID to look up",
    )
    parser.add_argument("--output", type=str, help="output filename")
    parser.add_argument("--exclude", type=str, default="", help="regex to exclude")
    parser.add_argument(
        "--ignore-closed",
        action="store_true",
        help="do not traverse into issues in the 'verify' or 'closed' state",
    )

    args = parser.parse_args()

    system_identifier = radarclient.ClientSystemIdentifier("OdieIssueClient", "1.0")
    radar_client = radarclient.RadarClient(
        radarclient.AuthenticationStrategySPNego(), system_identifier
    )

    issue = radar_client.radar_for_id(args.query_id)

    node_dict = {issue.id: node_for_issue(issue)}

    graph = pydot.Dot(f"rdar://{args.query_id}", graph_type="digraph", compound="true")
    graph.add_node(node_dict[issue.id])

    if len(args.exclude) != 0:
        exclude_regex = re.compile(args.exclude)
    else:
        exclude_regex = None

    try:
        related = issue.all_relationships(
            delegate=DownwardTraversalDelegate(exclude_regex, args.ignore_closed)
        )
    except radarclient.exceptions.RadarAccessDeniedResponseException:
        related = []

    relations = set()
    for chain in related:
        for r in chain:
            if r in relations:
                continue

            relations.add(r)

            print(r.radar)
            # Make sure we have nodes for the original and related issues.
            if r.radar.id not in node_dict:
                node_dict[r.radar.id] = node_for_issue(r.radar)
            if r.related_radar.id not in node_dict:
                node_dict[r.related_radar.id] = node_for_issue(r.related_radar)

            graph.add_node(node_dict[r.radar.id])
            graph.add_node(node_dict[r.related_radar.id])

            # Add the relationship edge
            graph.add_edge(
                pydot.Edge(f"{r.radar.id}", f"{r.related_radar.id}", label=r.type)
            )

    print("Done querying issues, writing output...")
    graph.write_svg(args.output)
