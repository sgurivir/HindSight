#!/usr/local/bin/python3

import argparse
import logging
import re
import tempfile
import subprocess
import os
from time import sleep
from radarclient import RadarClient, AuthenticationStrategySPNego, ClientSystemIdentifier, Relationship

sysdiagnoseRE = re.compile(r'sysdiagnose[^/]*\.tar\.gz$')

class IssueManager:
    def __init__(self):
        system_identifier = ClientSystemIdentifier('AKSIssueManager', '1.0')
        self.radar_client = RadarClient(AuthenticationStrategySPNego(), system_identifier)


    def fetch_fix_issue(self, fix_issue_id):
        print(f'Fetching rdar://{fix_issue_id}....')
        issue = self.radar_client.radar_for_id(fix_issue_id)
        print(f'Found rdar://{issue.id} ({issue.title})')
        component_name = issue.component.get('name')
        component_version = issue.component.get('version')
        if component_name != 'AppleKeyStore' or component_version != 'Domains':
            print(f'ERROR: rdar://{fix_issue_id} is in the wrong component: {component_name} | {component_version}')
            return None
        if issue.state != 'Analyze':
            print(f'ERROR: rdar://{fix_issue_id} is in the wrong state: {issue.state}')
            return None
        if issue.substate != 'Investigate':
            print(f'ERROR: rdar://{fix_issue_id} is in the wrong substate: {issue.state}.{issue.substate}')
            return None
        return issue


    def fetch_issues(self, issue_ids):
        print(f'Fetching issues: {issue_ids}')
        issues = self.radar_client.radars_for_ids(issue_ids, additional_fields=['relatedProblems'])
        print(f'Found {len(issues)} issue(s).')
        return issues


    def fetch_unscreened_issues(self):
        print('Fetching unscreened issues...')
        query = {
            'component': {
                'name': 'AppleKeyStore',
                'version': 'Domains'
            },
            'state': 'Analyze',
            'substate': 'Screen'
        }
        issues = self.radar_client.find_radars(query, additional_fields=['relatedProblems'])
        print(f'Found {len(issues)} unscreened issue(s).')
        return issues


    def list_unscreened_issues(self):
        issue_list = self.fetch_unscreened_issues()
        for issue in issue_list:
            print(f'rdar://{issue.id} ({issue.title})')


    def duplicate(self, issue, fix_issue):
        print(f'duplicating rdar://{issue.id} to rdar://{fix_issue.id}')
        issue.state = 'Verify'
        issue.resolution = 'Duplicate'
        issue.duplicateOfProblemID = fix_issue.id
        issue.commit_changes()


    def screen_issues(self, fix_issue, issue_ids):
        if issue_ids:
            issue_list = self.fetch_issues(issue_ids)
        else:
            issue_list = self.fetch_unscreened_issues()
        for issue in issue_list:
            if not 'Data Protection Domain policy violation detected' in issue.title:
                print(f'Skipping rdar://{issue.id} ({issue.title})')
            else:
                self.duplicate(issue, fix_issue)
                sleep(0.5) # for rate limiting
        print(f'Screened {len(issue_list)} issues.')


    def list_issues(self, fix_issue):
        print(f'Fetching duplicates of {fix_issue.id}...')
        issue_list = fix_issue.related_radars([Relationship.TYPE_ORIGINAL_OF])
        print(f'Found {len(issue_list)} issues:')
        for issue in issue_list:
            print(f'rdar://{issue.id} ({issue.title})')


    def analyze_sysdiagnose(self, issue, attachment):
        print(f'Analyzing rdar://{issue.id} attachment {attachment.fileName}...')
        with tempfile.TemporaryDirectory(prefix=f'issue{issue.id}-') as tmpdir:
            sysdiagnose_file = f'{tmpdir}/sysdiagnose.tgz'
            with open(sysdiagnose_file, 'wb') as fp:
                attachment.write_to_file(fp)
            tar = subprocess.run(['tar', '-xz', '-f', sysdiagnose_file, '-C', tmpdir], text=True, capture_output=True)
            if tar.returncode:
                print(f'tar exited with code {tar.returncode}')
                print(f'output: {tar.stdout}')
                print(f'errors: {tar.stderr}')
                print(f'failed to unpack sysdiagnose for rdar://{issue.id}')
                return
            predicate = '(subsystem == "ProtectionDomainManager" || sender BEGINSWITH "AKSAnalytics") && eventMessage BEGINSWITH "VIOLATION"'
            violations_file = f'violations_issue{issue.id}.txt'
            with open(violations_file, 'a') as outfp:
                print(f'*** Violations from {attachment.fileName} ***', file=outfp)
                outfp.flush()
                subprocess.run(['find', tmpdir, '-name', 'system_logs.logarchive', '-exec', 'log', 'show', '--archive', '{}', '--predicate', predicate, ';'], stdout=outfp, text=True, check=True)
            print(f'Wrote violations to {violations_file}.')


    def analyze_attachments(self, issue):
        found_attachments = False
        for attachment in issue.attachments.items():
            if sysdiagnoseRE.search(attachment.fileName):
                self.analyze_sysdiagnose(issue, attachment)
                found_attachments = True
        if not found_attachments:
            print(f'WARNING: no attachments in rdar://{issue.id}')


    def analyze_issues(self, fix_issue, issue_ids):
        if issue_ids:
            issue_list = self.fetch_issues(issue_ids)
        else:
            issue_list = fix_issue.related_radars([Relationship.TYPE_ORIGINAL_OF])
        for issue in issue_list:
            if os.path.exists(f'violations_issue{issue.id}.txt'):
                print(f'Skipping rdar://{issue.id} (already processed)')
            else:
                self.analyze_attachments(issue)


def main():
    parser = argparse.ArgumentParser(description='prepare new data protection domain violation issues for processing')
    parser.add_argument('--fix', help='issue id of the fix issue')
    parser.add_argument('--screen', action='store_true')
    parser.add_argument('--analyze', action='store_true')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--issue', action='append', help='process the specified issue(s)')
    parser.add_argument('--unscreened', action='store_true')
    args = parser.parse_args()

    logging.basicConfig()
    manager = IssueManager()

    if args.fix:
        fix_issue = manager.fetch_fix_issue(args.fix)
        if not fix_issue:
            print('Aborting.')
            return
    else:
        if args.screen or args.list or (args.analyze and not args.issue):
            print('--fix option is required')
            return
        fix_issue = None

    if args.unscreened:
        manager.list_unscreened_issues()

    if args.screen:
        manager.screen_issues(fix_issue, args.issue)

    if args.list:
        manager.list_issues(fix_issue)

    if args.analyze:
        manager.analyze_issues(fix_issue, args.issue)


if __name__ == '__main__':
    main()
