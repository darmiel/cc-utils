import collections
import dataclasses
import json
import logging
import time
from typing import Optional

import git
import git.exc
from github3 import GitHub
import github3.exceptions as gh3e
import github3.pulls as gh3p
import github3.structs as gh3s
import semver
import yaml
import yaml.scanner

import release_notes.model as rnm

_meta_key = 'gardener.cloud/release-notes-metadata/v1'
logger = logging.getLogger(__name__)


# pylint: disable=protected-access
# noinspection PyProtectedMember
def list_associated_pulls(gh: GitHub, owner: str, repo: str, sha: str) -> Optional[tuple[gh3p.ShortPullRequest]]:
    ''' Returns a tuple with pull requests related to the specified commit.

    :param gh: Instance of the GitHub v3 API
    :param owner: Owner of the repository (on GitHub)
    :param repo: Name of the repository (on GitHub)
    :param sha: SHA of the commit
    :return: a tuple with pull requests related to the specific commit
    '''
    try:
        url = gh._build_url('repos', owner, repo, 'commits', sha, 'pulls')
        return tuple(gh._iter(-1, url, gh3p.ShortPullRequest))
    except gh3e.UnprocessableEntity as e:
        return None


def _write_to_git_notes(repo: git.Repo, commit: git.Commit, body: str):
    ''' Notes can be attached to a commit using
    `$ git notes add -m <message> <sha>`

    :param repo: the repository the commit belongs to
    :param commit: the commit to add notes to
    :param body: the body to write to the commit notes
    :return:
    '''
    print("writing\n", body, "\nto", commit.hexsha, "\n")
    repo.git.notes('add', '-f', '-m', body, commit.hexsha)


# pylint: disable=protected-access
# noinspection PyProtectedMember
def list_pulls(gh: GitHub, owner: str, repo: str, state: str = 'closed') -> gh3s.GitHubIterator[gh3p.ShortPullRequest]:
    url = gh._build_url('repos', owner, repo, 'pulls') + '?state=' + state
    return gh._iter(-1, url, gh3p.ShortPullRequest)


def shorten(message: str, max_len: int = 128) -> str:
    message = message.replace('\n', '\\n')
    if len(message) > max_len:
        message = message[:max_len - 3] + '...'
    return message


def find_next_smallest_version(available_versions: list[semver.VersionInfo],
                                current_version: semver.VersionInfo) -> Optional[semver.VersionInfo]:
    # find version before the requested version and sort by semver
    return max((v for v in sorted(available_versions) if v < current_version), default=None)


def _find_git_notes_for_commit(repo: git.Repo, commit: git.Commit) -> Optional[str]:
    try:
        return repo.git.notes('show', commit.hexsha)
    except git.exc.GitCommandError:
        return None


def _find_payload_from_git_notes(repo: git.Repo, commit: git.Commit) -> Optional[dict]:
    ''' Notes can be read from a commit using
    `$ git notes show <sha>`

    :param repo: the repository the commit belongs to
    :param commit: the commit to read the payload from
    :return: the note contents parsed as JSON
    '''
    try:
        note: str = repo.git.notes('show', commit.hexsha)
        res = json.loads(note)
        if not isinstance(res, dict):
            raise RuntimeError('cannot convert payload from JSON to dict', note)
        return res
    except git.exc.GitCommandError:
        return None
    except json.JSONDecodeError:
        return None  # if the note doesn't contain valid JSON, we don't care about _that_ note


def _normalize_dict_keys(dic: dict, recursive: bool = False) -> dict:
    return {
        k.replace("-", "_").replace(" ", "_"): _normalize_dict_keys(v) if recursive and isinstance(v, dict) else v
        for k, v in dic.items()
    }


def _is_meta_document(doc) -> bool:
    return 'meta' in doc and isinstance(doc['meta'], dict) \
        and 'type' in doc['meta'] and isinstance(doc['meta']['type'], str) \
        and 'data' in doc['meta'] and isinstance(doc['meta']['data'], dict)


def _find_first_document(documents: list, key: str, cls):
    for doc in documents:
        if not _is_meta_document(doc):
            continue
        if doc['meta']['type'] != key:
            continue
        return cls(**doc['meta']['data'])
    return None


def _upsert_document(documents: list, key: str, instance):
    index = None
    for i, doc in enumerate(documents):
        if not _is_meta_document(doc):
            continue
        if doc['meta']['type'] == key:
            index = i
    if index is not None:
        documents[index] = instance
    else:
        documents.append(instance)


def request_pull_requests_from_api(repo: git.Repo,
                                   gh: GitHub,
                                   owner: str,
                                   repo_name: str,
                                   commits: list[git.Commit]) -> dict[str, list[gh3p.ShortPullRequest]]:
    ''' This function requests pull requests from the GitHub API and returns a dictionary mapping
    commit SHA to a list of pull requests.

    We use notes to store the associated pull request numbers to reduce requests to GitHub (rate limiting).
    The corresponding pull request number is stored in a note.
    We can then fetch a list of pull requests for a repository and thus (theoretically) process
    100 pull requests with one API call in the best case.

    If there is no note, request the "normal" API route to retrieve associated pull requests and
    store the pull-numbers in the commit note.
    '''
    # pr_number -> [ list of commit sha ]
    pending = collections.defaultdict(list)
    # commit_sha -> [ list of pull requests ]
    result = collections.defaultdict(list)

    for commit in commits:
        yaml_documents = []
        is_yaml_content = True
        if note_content := _find_git_notes_for_commit(repo, commit):
            try:
                yaml_documents = list(yaml.safe_load_all(note_content))
            except yaml.scanner.ScannerError:  # YAML parsing error
                is_yaml_content = False

        # if there is already a ReleaseNotesMetadata
        if nums_meta := _find_first_document(yaml_documents,
                                             _meta_key,
                                             rnm.ReleaseNotesMetadata):
            for num in nums_meta.prs:
                pending[num].append(commit.hexsha)
            continue

        if prs := list_associated_pulls(gh, owner, repo_name, commit.hexsha):
            # add all found pull requests to the result right away
            result[commit.hexsha].extend(prs)
            # only write notes to commit if there are no notes yet,
            # or if the notes are in the YAML format already
            if not note_content or is_yaml_content:
                data = dataclasses.asdict(rnm.ReleaseNotesMetadata(round(time.time() * 1000), [z.number for z in prs]))
                meta = rnm.get_meta_obj(_meta_key, data)
                _upsert_document(yaml_documents, _meta_key, meta)
                _write_to_git_notes(repo, commit, yaml.safe_dump_all(yaml_documents))

    if pending:
        for pull in list_pulls(gh, owner, repo_name):
            if pull.number in pending:
                for sha in pending[pull.number]:
                    result[sha].append(pull)
                del pending[pull.number]
            if len(pending) == 0:
                break
        else:
            logger.warning(f'one or more associated pull requests for the commits ' +
                           f'{pending.keys()} is/are either not closed or cannot be found')

    return result
