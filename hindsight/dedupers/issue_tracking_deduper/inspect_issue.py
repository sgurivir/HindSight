#!/usr/bin/env python3
"""
Script to query an issue by ID and inspect its structure to find description access.
"""

import radarclient
from radarclient import RadarClient, AuthenticationStrategySPNego, ClientSystemIdentifier

# Create client (using radarclient library for Apple's Radar API)
system_identifier = ClientSystemIdentifier('IssueInspector', '1.0')
radar_client = RadarClient(AuthenticationStrategySPNego(), system_identifier)

# Query the issue
issue_id = 167638331
print(f"Fetching issue {issue_id}...")
issue = radar_client.radar_for_id(issue_id)

print(f"\n=== Issue {issue_id} ===")
print(f"Title: {issue.title}")
print(f"State: {issue.state}")

# Introspect the issue object
print("\n=== Object Introspection ===")
print(f"Type: {type(issue)}")

# Get all attributes and methods
print("\n--- All attributes (dir) ---")
attrs = [a for a in dir(issue) if not a.startswith('_')]
for attr in sorted(attrs):
    print(f"  {attr}")

# Try to access description
print("\n=== Accessing Description ===")
try:
    desc = issue.description
    print(f"Description type: {type(desc)}")
    print(f"Description dir: {[a for a in dir(desc) if not a.startswith('_')]}")
    
    # Try items() method
    if hasattr(desc, 'items'):
        items = desc.items()
        print(f"\nDescription items ({len(items)} entries):")
        for i, item in enumerate(items):
            print(f"\n  Item {i} type: {type(item)}")
            print(f"  Item {i} dir: {[a for a in dir(item) if not a.startswith('_')]}")
            # Try to get the text content
            if hasattr(item, 'text'):
                print(f"  Item {i} text: {item.text[:200] if len(item.text) > 200 else item.text}...")
            elif hasattr(item, 'content'):
                content = str(item.content)
                print(f"  Item {i} content: {content[:200] if len(content) > 200 else content}...")
            else:
                print(f"  Item {i} str: {str(item)[:200]}...")
            if i >= 2:  # Only show first 3 items
                print(f"\n  ... and {len(items) - 3} more items")
                break
except Exception as e:
    print(f"Error accessing description: {e}")

print("\n=== Done ===")
