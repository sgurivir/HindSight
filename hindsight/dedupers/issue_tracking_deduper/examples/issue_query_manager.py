#!/user/bin/env python3

import os
import re
from pathlib import Path
import tarfile
from typing import Optional
from syslog import LOG_ERR, LOG_INFO, LOG_CRIT, LOG_WARNING
import radarclient # https://liyanage.apple.com/software/radarclient-python/
from radar_spotlight import radar_logger

# Encapsulates methods to execute queries.
class IssueQueryManager:
    # issue_id: id of the issue
    def __init__(self, query_id):
        super().__init__()
        system_identifier = radarclient.ClientSystemIdentifier('SpotlightStressTestScreener', '1.0')
        self.client = radarclient.RadarClient(radarclient.AuthenticationStrategySPNego(), system_identifier)
        #self.issue_query = radar_client.query_for_id(query_id)
        self.query_id=query_id

    def execute(self) -> list[int]:
        issues = self.client.radars_for_query(self.query_id)
        ids = []
        for index,issue in  enumerate(issues):
            id=issue.id
            print(f"{index:>5}|{id}")
            ids.append(id)
        return ids

