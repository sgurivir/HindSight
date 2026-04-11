#!/usr/bin/env python3
import argparse
import subprocess
import tempfile
import os
import shutil
import datetime

def get_current_branch(repo_dir):
    """Get the currently checked out branch in the repository."""
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo_dir, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        # Fallback: try to get HEAD reference
        try:
            result = subprocess.run(
                ["git", "symbolic-ref", "--short", "HEAD"],
                cwd=repo_dir, capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

def get_default_branch_info(repo_url):
    """Get default branch name and commit hash using ls-remote."""
    try:
        # Get default branch reference
        result = subprocess.run(
            ["git", "ls-remote", "--symref", repo_url, "HEAD"],
            capture_output=True, text=True, check=True, timeout=30
        )

        default_branch = None
        head_commit = None

        for line in result.stdout.splitlines():
            if line.startswith("ref: refs/heads/"):
                default_branch = line.split("refs/heads/")[1]
            elif not line.startswith("ref:") and line.strip():
                head_commit = line.split()[0]
                break

        return default_branch, head_commit
    except Exception:
        return None, None

def get_commit_date_from_temp_repo(repo_url, commit_hash):
    """Get commit date by creating a minimal temporary repo."""
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix="git_commit_")

        # Initialize bare repo and fetch specific commit
        subprocess.run(["git", "init", "--bare"], cwd=temp_dir, capture_output=True, check=True)
        subprocess.run(["git", "remote", "add", "origin", repo_url], cwd=temp_dir, capture_output=True, check=True)

        # Fetch just the specific commit
        fetch_result = subprocess.run(
            ["git", "fetch", "origin", commit_hash],
            cwd=temp_dir, capture_output=True, text=True, timeout=30
        )

        if fetch_result.returncode == 0:
            # Get commit date
            date_result = subprocess.run(
                ["git", "show", "-s", "--format=%ci", commit_hash],
                cwd=temp_dir, capture_output=True, text=True, timeout=10
            )

            if date_result.returncode == 0 and date_result.stdout.strip():
                date_str = date_result.stdout.strip()
                # Parse: 2023-05-31 14:23:45 +0000
                date_part = date_str.split(' +')[0].split(' -')[0]
                return datetime.datetime.fromisoformat(date_part.replace(' ', 'T'))

        return None
    except Exception:
        return None
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except:
                pass

def list_remote_branches(repo_url: str = None, repo_dir: str = None, recent_days: int = None):
    temp_dir = None
    original_branch = None
    working_dir = None
    cutoff_date = None

    try:
        if repo_dir:
            # Use existing repository directory
            if not os.path.exists(repo_dir):
                raise Exception(f"Repository directory does not exist: {repo_dir}")
            if not os.path.exists(os.path.join(repo_dir, ".git")):
                raise Exception(f"Directory is not a git repository: {repo_dir}")

            working_dir = repo_dir
            original_branch = get_current_branch(repo_dir)

            # Get remote URL if not provided
            if not repo_url:
                remote_url_result = subprocess.run(
                    ["git", "remote", "get-url", "origin"],
                    cwd=working_dir, capture_output=True, text=True, check=True
                )
                repo_url = remote_url_result.stdout.strip()

            print("Updating remote references...")
            subprocess.run(
                ["git", "fetch", "--all"],
                cwd=working_dir, capture_output=True, check=False
            )

        # Get all remote branches using ls-remote (fast)
        print("Getting remote branch list...")
        ls_remote_result = subprocess.run(
            ["git", "ls-remote", "--heads", repo_url],
            capture_output=True, text=True, check=True, timeout=30
        )

        lines = ls_remote_result.stdout.strip().splitlines()
        if not lines:
            print("No branches found.")
            return

        # Set cutoff date if recent_days is specified
        if recent_days:
            print("Getting default branch commit date as reference...")
            default_branch, head_commit = get_default_branch_info(repo_url)

            if head_commit:
                if repo_dir:
                    # Try to get date from existing repo first
                    try:
                        date_result = subprocess.run(
                            ["git", "show", "-s", "--format=%ci", head_commit],
                            cwd=working_dir, capture_output=True, text=True, timeout=5
                        )
                        if date_result.returncode == 0 and date_result.stdout.strip():
                            date_str = date_result.stdout.strip()
                            date_part = date_str.split(' +')[0].split(' -')[0]
                            default_date = datetime.datetime.fromisoformat(date_part.replace(' ', 'T'))
                        else:
                            default_date = get_commit_date_from_temp_repo(repo_url, head_commit)
                    except:
                        default_date = get_commit_date_from_temp_repo(repo_url, head_commit)
                else:
                    default_date = get_commit_date_from_temp_repo(repo_url, head_commit)

                if default_date:
                    cutoff_date = default_date - datetime.timedelta(days=recent_days)
                    print(f"Default branch '{default_branch or 'HEAD'}' last commit: {default_date.strftime('%Y-%m-%d %H:%M:%S')}")
                    print(f"Filtering branches with commits after: {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')}")
                else:
                    print("Could not get default branch commit date, using current date as fallback")
                    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=recent_days)
            else:
                print("Could not determine default branch, using current date as fallback")
                cutoff_date = datetime.datetime.now() - datetime.timedelta(days=recent_days)

        print(f"Found {len(lines)} branches. Processing...")
        print(f"{'Commit Hash':<12}  {'Date Modified':<20}  Branch")
        print("-" * 60)

        branches_shown = 0
        branches_processed = 0

        # Create temporary repo for commit date lookups if needed
        if not repo_dir:
            temp_dir = tempfile.mkdtemp(prefix="git_branches_")
            subprocess.run(["git", "init", "--bare"], cwd=temp_dir, capture_output=True, check=True)
            subprocess.run(["git", "remote", "add", "origin", repo_url], cwd=temp_dir, capture_output=True, check=True)
            working_dir = temp_dir

        for line in lines:
            commit_hash, ref = line.split()
            branch = ref.replace("refs/heads/", "")
            branches_processed += 1

            if branches_processed % 10 == 0:
                print(f"Processed {branches_processed}/{len(lines)} branches...", end='\r')

            commit_datetime = None
            commit_date = "unknown"

            # Get commit date
            try:
                if repo_dir:
                    # Try existing repo first
                    date_result = subprocess.run(
                        ["git", "show", "-s", "--format=%ci", commit_hash],
                        cwd=working_dir, capture_output=True, text=True, timeout=5
                    )
                    if date_result.returncode == 0 and date_result.stdout.strip():
                        date_str = date_result.stdout.strip()
                        date_part = date_str.split(' +')[0].split(' -')[0]
                        commit_datetime = datetime.datetime.fromisoformat(date_part.replace(' ', 'T'))
                        commit_date = commit_datetime.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    # Fetch specific commit and get date
                    fetch_result = subprocess.run(
                        ["git", "fetch", "origin", commit_hash],
                        cwd=working_dir, capture_output=True, timeout=10
                    )
                    if fetch_result.returncode == 0:
                        date_result = subprocess.run(
                            ["git", "show", "-s", "--format=%ci", commit_hash],
                            cwd=working_dir, capture_output=True, text=True, timeout=5
                        )
                        if date_result.returncode == 0 and date_result.stdout.strip():
                            date_str = date_result.stdout.strip()
                            date_part = date_str.split(' +')[0].split(' -')[0]
                            commit_datetime = datetime.datetime.fromisoformat(date_part.replace(' ', 'T'))
                            commit_date = commit_datetime.strftime("%Y-%m-%d %H:%M:%S")

            except Exception:
                # If we can't get the date and we're filtering, skip this branch
                if recent_days:
                    continue
                commit_date = "unknown"

            # Apply date filter if specified
            if cutoff_date and commit_datetime and commit_datetime < cutoff_date:
                continue

            # Display short commit hash (first 8 characters)
            short_hash = commit_hash[:8]
            print(f"{short_hash:<12}  {commit_date:<20}  {branch}")
            branches_shown += 1

        print(f"\nProcessed {branches_processed} branches.")
        if recent_days:
            print(f"Showing {branches_shown} branches (filtered by {recent_days} days)")
        else:
            print(f"Showing {branches_shown} branches")

    except subprocess.CalledProcessError as e:
        print("Error running git command:", e.stderr or str(e))
    except Exception as e:
        print(f"Error: {str(e)}")
    finally:
        # Restore original branch if we were using an existing repo
        if repo_dir and original_branch and repo_dir == working_dir:
            try:
                current_branch = get_current_branch(working_dir)
                if current_branch != original_branch:
                    subprocess.run(
                        ["git", "checkout", original_branch],
                        cwd=working_dir, capture_output=True, check=False
                    )
            except Exception as e:
                print(f"Warning: Could not restore original branch '{original_branch}': {e}")

        # Clean up temporary directory
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                print(f"Warning: Could not remove temporary directory {temp_dir}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="List remote branches with commit hash and last commit dates using standard git commands."
    )
    parser.add_argument(
        "--repo_url",
        help="Git repository URL (e.g. https://github.com/user/repo.git). Required if --repo_dir is not provided."
    )
    parser.add_argument(
        "--repo_dir",
        help="Path to existing local git repository directory. If provided, --repo_url is optional."
    )
    parser.add_argument(
        "--recent-days",
        type=int,
        help="Only show branches with commits within the specified number of days before the default branch's last commit."
    )
    args = parser.parse_args()

    # Validate arguments
    if not args.repo_url and not args.repo_dir:
        parser.error("Either --repo_url or --repo_dir must be provided.")

    list_remote_branches(args.repo_url, args.repo_dir, args.recent_days)
