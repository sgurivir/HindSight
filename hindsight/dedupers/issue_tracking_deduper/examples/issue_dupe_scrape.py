#!/usr/bin/env python

# see https://engweb.apple.com/pypi/radarclient and
# http://liyanage.apple.com/software/radarclient-python/ for API docs

import argparse
import os
import re
import pdb
import sys
import json
import subprocess

try:
    from parse import *
except:
    print('cannot import parse')
    print('\tinstall with: pip install parse')
    exit(1)

try:
    from tabulate import tabulate
except:
    print('cannot import tabulate')
    print('\tinstall with: pip install tabulate')
    exit(1)

try:
    import radarclient
    from radarclient import RadarClient, AuthenticationStrategySPNego, Person, ClientSystemIdentifier
except ImportError:
    print('cannot import radarclient')
    print('\tinstall with: pip install -i http://pypi.apple.com/simple radarclient')
    exit(1)

ACTION = "actions"
ACTION_TYPE = "type"
LLDB_ACTION_TYPE = "lldb"
ATTACH_PY_ACTION_TYPE = "attach_py"
TYPE_LIST = [ LLDB_ACTION_TYPE, ATTACH_PY_ACTION_TYPE ]

LLDB_ACTION_ATT = "attachment"
LLDB_ACTION_SCRIPTS = "scripts"
LLDB_ACTION_SDK_VARIANT = "sdk_variant"
LLDB_ACTION_LOADSYM = "loadsym"
LLDB_ACTION_SRC_TMP_PATH = "src_tmp_file_name"
LLDB_ACTION_DST_TMP_PATH = "dst_tmp_file_name"
LLDB_ACTION_SAVE = "save"

ATTACH_PY_ACTION_ATT = "attachment"
ATTACH_PY_ACTION_SCRIPT = "script"
ATTACH_PY_ACTION_SRC_TMP_PATH = "src_tmp_file_name"
ATTACH_PY_ACTION_DST_TMP_PATH = "dst_tmp_file_name"
ATTACH_PY_ACTION_SAVE = "save"

parser = argparse.ArgumentParser(description='Find issues containing attachment recursively')
parser.add_argument('-r', metavar='Issue_ID', type=int, required=False,
    help='Issue ID for file attachment')
parser.add_argument('-rf', metavar='Issue_ID_file', type=str, required=False,
    help='File with issue link list')
parser.add_argument('-f', metavar='Filename', type=str, required=False,
    help='Attachment name to search for')
parser.add_argument('-s', metavar='String', type=str, required=False,
    help='String in attachment name to search for')
parser.add_argument('-st', metavar='String', type=str, required=False,
    help='String in title name to search for')
parser.add_argument('-sdesc', metavar='String', type=str, required=False,
    help='String in description name to search for')
parser.add_argument('-sdiag', metavar='String', type=str, required=False,
    help='String in discription/diagnosis name to search for')
parser.add_argument('-d', metavar='String', type=str, required=False,
    help='Issue to redupe matching issues to')
parser.add_argument('-pf', metavar='Post_processing_file', type=str, required=False,
    help='File with info about post processing')
parser.add_argument('-c', required=False,
    help='Add Diagnosis Comment when reduping', action="store_true")

# Note: requires python3 for processing action invocations (-pf argument)

# Example contents for file passed through -rf argument:
# <rdar://problem/64116420> D53gAP/Azul18A306x: panic: DCP PANIC - apt firmware: mcpu.c:1900 debug_error_handler() -- - iomfb_mailbox(44)
# <rdar://problem/64115502> D52gAP/Azul18A306x: panic: DCP PANIC - apt firmware: mcpu.c:1900 debug_error_handler() -- - iomfb_mailbox(44)
# <rdar://problem/64114708> D52gAP/Azul18A306x: panic: DCP PANIC - apt firmware: mcpu.c:1900 debug_error_handler() -- - iomfb_mailbox(44)
# <rdar://problem/64114456> D52gAP/Azul18A306x: panic: DCP PANIC - apt firmware: mcpu.c:1900 debug_error_handler() -- - iomfb_mailbox(44)
# <rdar://problem/64114311> D53gAP/Azul18A306x: panic: DCP PANIC - apt firmware: mcpu.c:1900 debug_error_handler() -- - iomfb_mailbox(44)
# <rdar://problem/64114117> D53gAP/Azul18A306x: panic: DCP PANIC - apt firmware: mcpu.c:1900 debug_error_handler() -- - iomfb_mailbox(44)
# <rdar://problem/64113785> D53gAP/Azul18A306x: panic: DCP PANIC - apt firmware: mcpu.c:1900 debug_error_handler() -- - iomfb_mailbox(44)

# Example invocations:
#  python issue_dupe_scrape.py -r 64420009 -f disp.triage.txt -s disp_has_no_client -d 64139528 -c
#  python issue_dupe_scrape.py -r 64685306 -pf sample_processing_actions.json

# Can also combine the above and use output from action to dupe like:
#  python issue_dupe_scrape.py -r 64685306 -pf sample_processing_actions.json
#                  -f iomfb_dcp_triage.txt -s "unplug_notif_pending = 0x1" -d 64239326 -c

args = parser.parse_args()
system_identifier = ClientSystemIdentifier('IssueCLI', '1.0')
radar_client = RadarClient(AuthenticationStrategySPNego(), system_identifier)

tackled_issues = set()
has_att = set()
has_string = set()
reduped_issues = set()

def bordered( text ):
    table = [[text]]
    output = tabulate(table, tablefmt='grid')
    return output

def redupe_issue( issue, redupe_issue_id ):
    if( redupe_issue_id is None or issue.id in reduped_issues ):
        return
    if( issue.state != 'Closed' ):
        issue.state = 'Verify'
    issue.resolution = 'Duplicate'
    issue.duplicateOfProblemID = int(redupe_issue_id)
    if( args.c ):
        entry = radarclient.DiagnosisEntry()
        entry.text = u'Diagnosed using command: %s' % (' '.join(sys.argv))
        issue.diagnosis.add(entry)
    try:
        issue.commit_changes()
        reduped_issues.add( issue.id )
    except Exception as e:
        print("Error %s for issue operation for issue %d" % (e, issue.id))

def postprocess_issue_lldb( issue, lldb_dict ):
    #print(lldb_dict)
    scripts = lldb_dict[LLDB_ACTION_SCRIPTS]
    attach_name = lldb_dict[LLDB_ACTION_ATT]
    variant = lldb_dict[LLDB_ACTION_SDK_VARIANT]
    loadsym = lldb_dict[LLDB_ACTION_LOADSYM]
    src_tmp_path = lldb_dict[LLDB_ACTION_SRC_TMP_PATH]
    dst_tmp_path = lldb_dict[LLDB_ACTION_DST_TMP_PATH]
    save_name = lldb_dict[LLDB_ACTION_SAVE]

    attachments = issue.attachments
    title = issue.title
    print("Issue id " + str(issue.id) + ": Processing LLDB action")

    if "J30" in title:
        binname = "ipad13pdcp.macho"
    elif "J51" in title or "J52" in title:
        binname = "ipad13dcp.macho"
    elif "N15" in title:
        binname = "watch6dcp.macho"
    elif "D5" in title:
        binname = "iphone13dcp.macho"
    else:
        print("Issue id " + str(issue.id) + ": Cannot identify target")
        return
    if "Azul" in title:
        base_build = "Azul"
        build = title.split(":")[0].split("/")[1]
    elif "Hunter" in title:
        base_build = "Hunter"
        build = title.split(":")[0].split("/")[1]
    else:
        print("Issue id " + str(issue.id) + ": Cannot identify build")
        return

    sym_command = ""
    if( bool(loadsym) ):
        sym_command = "/SWE/release/Software/" + base_build + "/Updates/" + build + "/Symbols/AppleDCP/" + binname

    if attachments is None:
        print("Issue id " + str(issue.id) + ": No attachments")
        return

    found = False
    for item in attachments.items():
        if item is None:
            continue
        if attach_name in item.fileName:
            if item is None:
                continue
            with open(src_tmp_path, "wb") as f:
                item.write_to_file(f)
            found = True
            break
    if not found:
        print("Issue id " + str(issue.id) + ": Could not find attachment " + attach_name)
        return

    base_command =  "xcrun -sdk %s lldb" % ( variant )
    core_path_command = "-c %s" %( src_tmp_path )
    script_commands = scripts
    command_list = [ base_command, core_path_command ] +  script_commands + [ sym_command ]
    command = ' '.join(command_list)

    try:
        print(command)
        out = subprocess.run( command, shell=True, text=True, stdout=subprocess.PIPE, timeout=20 )
        out_file = open( dst_tmp_path, "r+" )
        out_file.write( out.stdout )
        out_file.seek( 0 )

        save_attach = issue.new_attachment( save_name )
        save_attach.set_upload_content( str.encode(out_file.read()) )
        out_file.close()

        issue.attachments.add( save_attach )
        print("Issue id " + str(issue.id) + ": Generated LLDB output")
        try:
            issue.commit_changes()
            print("Issue id " + str(issue.id) + ": Uploaded LLDB output")
        except Exception as e0:
            print("Exception: %s" % str(e0))
            return
    except Exception as e1:
        print("Exception: %s" % str(e1))
        return

def postprocess_issue_attach_py( issue, attach_py_dict ):
    #print(attach_py_dict)
    script_file = attach_py_dict[ATTACH_PY_ACTION_SCRIPT]
    attach_name = attach_py_dict[ATTACH_PY_ACTION_ATT]
    save_name = attach_py_dict[ATTACH_PY_ACTION_SAVE]
    src_tmp_path = attach_py_dict[ATTACH_PY_ACTION_SRC_TMP_PATH]
    dst_tmp_path = attach_py_dict[ATTACH_PY_ACTION_DST_TMP_PATH]

    attachments = issue.attachments
    title = issue.title

    print("Issue id " + str(issue.id) + ": Processing Python parse action")

    if attachments is None:
        print("Issue id " + str(issue.id) + ": No attachments")
        return

    found = False
    for item in attachments.items():
        if item is None:
            continue
        if attach_name in item.fileName:
            if item is None:
                continue
            with open(src_tmp_path, "wb") as f:
                item.write_to_file(f)
            found = True
            break
    if not found:
        print("Issue id " + str(issue.id) + ": Could not find attachment " + attach_name)
        return

    base_command =  "python %s < " % ( script_file )
    out_command = " > %s" % ( dst_tmp_path )
    command_list = [base_command, src_tmp_path, out_command]
    command = ' '.join(command_list)

    try:
        print(command)
        out = subprocess.run( command, shell=True, text=True, stdout=subprocess.PIPE, timeout=20 )
        out_file = open( dst_tmp_path, "r+" )
        out_file.write( out.stdout )
        out_file.seek( 0 )

        save_attach = issue.new_attachment( save_name )
        save_attach.set_upload_content( str.encode(out_file.read()) )
        out_file.close()

        issue.attachments.add( save_attach )

        print("Issue id " + str(issue.id) + ": Generated python parsed output")
        try:
            issue.commit_changes()
            print("Issue id " + str(issue.id) + ": Uploaded python parsed output")
        except Exception as e0:
            print("Exception: %s" % str(e0))
            return
    except Exception as e1:
        print("Exception: %s" % str(e1))
        return

def postprocess_issue( issue, postprocess_dict ):
    if ACTION in postprocess_dict:
        print("Issue id " + str(issue.id) + ": " + issue.title)
        for action in postprocess_dict[ACTION]:
            if action[ACTION_TYPE] == LLDB_ACTION_TYPE:
                postprocess_issue_lldb( issue, action )
            if action[ACTION_TYPE] == ATTACH_PY_ACTION_TYPE:
                postprocess_issue_attach_py( issue, action )

def tackle_issue( issue_id, search_str, redupe_issue_id, postprocess_dict ):
    issue = None
    tackled_issues.add( issue_id )
    try:
        issue = radar_client.radar_for_id( issue_id )
    except:
        return

    title = u' '.join(issue.title).encode('utf-8').strip()

    postprocess_issue( issue, postprocess_dict )

    attachments = issue.attachments
    if args.f is not None and attachments is not None:
        for item in attachments.items():
            if item is None:
                continue
            if args.f in item.fileName:
                print("Issue id " + str(issue.id) + ": Found " + args.f)
                if search_str is not None:
                    content = str(item.content()).split('\n')
                    for line in content:
                        if search_str in line:
                            has_string.add(issue_id)
                            redupe_issue( issue, redupe_issue_id )
                            print(line)
                has_att.add(issue_id)

    if args.st is not None and args.st in issue.title:
        redupe_issue( issue, redupe_issue_id )
        print(issue.title)

    def match_with_string( items, search_str ):
        for item in items:
            content = str(item).split('\n')
            for line in content:
                if search_str in line:
                    redupe_issue( issue, redupe_issue_id )
                    print(line)

    if args.sdesc is not None:
        items = issue.description.items()
        match_with_string( items, args.sdesc )

    if args.sdiag is not None:
        items = issue.diagnosis.items()
        match_with_string( items, args.sdiag )

    print("Issue id " + str(issue.id) + ": Processed")

    for dupe in issue.related_radars():
        if( dupe.duplicateOfProblemID == issue_id ):
            if( dupe.id in tackled_issues ):
                continue
            else:
                tackle_issue( dupe.id, search_str, redupe_issue_id, postprocess_dict )

def main():
    issue_id = args.r
    issue_id_file = args.rf
    if issue_id is None and issue_id_file is None:
        exit( "Error: Need to provide atleast single issue id or file with issue link list." )
    search_str = args.s
    redupe_issue_id = args.d
    postprocess_file = args.pf
    postprocess_dict = {}
    top_issues_to_tackle = []
    if issue_id is not None:
        top_issues_to_tackle += [ issue_id ]
    if issue_id_file is not None:
        with open(issue_id_file) as fp:
            lines = fp.readlines()
            for line in lines:
                id = search("rdar://{:d}", line)
                if( id is not None ):
                    top_issues_to_tackle += [ id.fixed[0] ]

    if postprocess_file is not None:
        with open(postprocess_file) as fp:
            postprocess_dict = json.load( fp )
            print(bordered( postprocess_dict ))

    for rid in top_issues_to_tackle:
        tackle_issue( rid, search_str, redupe_issue_id, postprocess_dict )
    print(bordered( "Issues without file" ))
    print(sorted( set(tackled_issues) - set(has_att) ))
    print(bordered( "Issues with file" ))
    print(sorted( has_att ))
    print(bordered( "Issues with file and string" ))
    print(sorted( has_string ))
    print(bordered( "Issues with file but no string" ))
    print(sorted( has_att - has_string ))
    if redupe_issue_id is not None:
        print(bordered( "Issues reduped (file and string and redupe_issue_id provided)" ))
        print(sorted( reduped_issues ))

if __name__ == "__main__":
    main()
