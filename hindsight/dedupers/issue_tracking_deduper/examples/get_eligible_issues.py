import radarclient
import os
import sys
import argparse
from tqdm import tqdm
from datetime import datetime, timedelta
import json


def get_eligible_issues(gte, lte, output):
    # by default, we process the issues in last week, so lte is gte + 7 days
    if lte is None:
        dt = datetime.strptime(gte, "%Y-%m-%dT%H:%M:%S%z")
        lte = (dt + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S%z")

    system_identifier = radarclient.ClientSystemIdentifier(os.path.basename(sys.argv[0]), '1.0')
    radar_client = radarclient.RadarClient(radarclient.AuthenticationStrategySPNego(), system_identifier)

    query = {"component": {"name": "Search Tool"},
             "createdAt": {'gte': gte, 'lte': lte}
             }
    issue_ids = radar_client.find_radar_ids(query)
    existing_issues = read_issue_unittest()

    candidate_count = 0
    with open(output, "w") as fout:
        for i, issue_id in enumerate(tqdm(issue_ids, desc="Processing issues")):
            issue = radar_client.radar_for_id(issue_id)
            eligible = False
            GT = False
            GT_File = []

            for attachment in issue.attachments.items():
                if attachment.fileName.endswith('SearchTool.events.json') and issue_id not in existing_issues:
                    eligible = True
                if attachment.fileName.endswith(('.ics', '.eml', 'pkpass', '.JPEG', '.jpeg', '.jpg', '.png')):
                    GT = True
                    GT_File.append(attachment.fileName)
            if eligible and GT:
                candidate_count += 1
                print(f'{candidate_count} rdar://{issue_id} : {GT_File} \n')
                fout.write(f'rdar://{issue_id}\n')


def read_issue_unittest():
    existing_issues = set()
    with open('issue_unittests.jsonl') as fin:
        for line in fin:
            obj = json.loads(line)
            existing_issues.update(obj.keys())
    return existing_issues


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="issue detection parser")
    parser.add_argument("--gte", type=str, help="starting time, e.g., 2025-01-01T00:00:00+0000")
    parser.add_argument("--lte", type=str, required=False, help="end time, e.g., 2025-01-01T00:00:00+0000")
    parser.add_argument("--output", type=str, default='output/issue_candidates.txt', help="output directory")
    args = parser.parse_args()

    get_eligible_issues(args.gte, args.lte, args.output)
