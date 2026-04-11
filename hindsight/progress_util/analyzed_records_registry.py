import os
import pickle
import json
from pathlib import Path

from ..utils.hash_util import HashUtil


class AnalyzedRecordsRegistry:
    """
    Registry for tracking analyzed records to avoid duplicate analysis.

    Stores records in a dictionary with hash of record as key.
    Automatically loads prior records on initialization and provides
    methods to add, check, and persist analyzed records.
    """

    def __init__(self, proj_name, stored_results_directory=None):
        """
        Initialize the AnalyzedRecordsRegistry.

        Args:
            proj_name (str): Name of the project
            stored_results_directory (str, optional): Directory to store results.
                                                    If None, uses $HOME/.hindsight/proj_name
        """
        self.proj_name = proj_name
        self.analyzed_records = {}
        self.records_since_last_commit = 0

        # Set up storage directory
        if stored_results_directory is None:
            home_dir = Path.home()
            self.storage_dir = home_dir / ".hindsight" / proj_name
        else:
            self.storage_dir = Path(stored_results_directory)

        # Create directory if it doesn't exist
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        # Set up pickle file path
        self.pickle_file_path = self.storage_dir / "analyzedRecords.pkl"

        # Load existing records
        self._load_existing_records()

    def _load_existing_records(self):
        """Load existing analyzed records from pickle file if it exists."""
        if self.pickle_file_path.exists():
            try:
                with open(self.pickle_file_path, 'rb') as f:
                    self.analyzed_records = pickle.load(f)
                print(f"Loaded {len(self.analyzed_records)} existing analyzed records")
            except (pickle.PickleError, EOFError, FileNotFoundError) as e:
                print(f"Warning: Could not load existing records: {e}")
                self.analyzed_records = {}
        else:
            print("No existing analyzed records found, starting fresh")

    def _hash_record(self, record):
        """
        Generate a hash for the given record.

        Args:
            record: The record to hash (can be string, dict, or other serializable object)

        Returns:
            str: SHA256 hash of the record
        """
        return HashUtil.hash_for_record_sha256(record)

    def add_analyzed(self, record):
        """
        Add a record to the analyzed records dictionary.
        Automatically commits every 10 records.

        Args:
            record: The record to mark as analyzed
        """
        record_hash = self._hash_record(record)
        self.analyzed_records[record_hash] = {
            'record': record,
            'analyzed_at': str(Path().absolute()),  # Store current working directory as context
            'hash': record_hash
        }
        self.records_since_last_commit += 1
        print(f"Added record with hash {record_hash[:8]}... to analyzed records")

        # Auto-commit every 10 records
        if self.records_since_last_commit >= 10:
            self.commit()
            self.records_since_last_commit = 0

    def is_analyzed(self, record):
        """
        Check if a record has already been analyzed.

        Args:
            record: The record to check

        Returns:
            bool: True if the record has been analyzed, False otherwise
        """
        record_hash = self._hash_record(record)
        return record_hash in self.analyzed_records

    def commit(self):
        """
        Save the analyzed records to the pickle file.

        Persists the current state of analyzed_records to
        stored_results_directory/analyzedRecords.pkl
        """
        try:
            with open(self.pickle_file_path, 'wb') as f:
                pickle.dump(self.analyzed_records, f)
            print(f"Successfully saved {len(self.analyzed_records)} analyzed records to "
                  f"{self.pickle_file_path}")
            self.records_since_last_commit = 0  # Reset counter after successful commit
        except Exception as e:
            print(f"Error saving analyzed records: {e}")
            raise

    def get_stats(self):
        """
        Get statistics about the analyzed records.

        Returns:
            dict: Statistics including count and storage location
        """
        return {
            'total_analyzed': len(self.analyzed_records),
            'storage_directory': str(self.storage_dir),
            'pickle_file': str(self.pickle_file_path),
            'project_name': self.proj_name
        }

    def clear_all(self):
        """
        Clear all analyzed records (useful for testing or reset).
        """
        self.analyzed_records.clear()
        self.records_since_last_commit = 0
        print("Cleared all analyzed records from memory")

    def delete_and_start_over(self):
        """
        Delete the pickle file and start over with a clean slate.
        This removes all persisted analyzed records permanently.
        """
        try:
            if self.pickle_file_path.exists():
                self.pickle_file_path.unlink()
                print(f"Deleted pickle file: {self.pickle_file_path}")
            else:
                print(f"Pickle file does not exist: {self.pickle_file_path}")

            # Clear in-memory records
            self.analyzed_records.clear()
            self.records_since_last_commit = 0
            print("Started over with clean slate - all analyzed records removed")

        except Exception as e:
            print(f"Error deleting pickle file: {e}")
            raise

    def __len__(self):
        """Return the number of analyzed records."""
        return len(self.analyzed_records)

    def __contains__(self, record):
        """Allow using 'in' operator to check if record is analyzed."""
        return self.is_analyzed(record)
