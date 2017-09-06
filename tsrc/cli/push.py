""" Entry point for tsrc push """

import abc
import re
import unidecode

import ui
import tsrc.config
import tsrc.gitlab
import tsrc.git
import tsrc.cli


WIP_PREFIX = "WIP: "


def get_gitlab_token():
    schema = {"auth": {"gitlab": {"token": str}}}
    config = tsrc.config.parse_tsrc_config(schema=schema)
    return config["auth"]["gitlab"]["token"]


def get_project_url(repo_path):
    rc, out = tsrc.git.run_git(repo_path, "remote", "get-url", "origin", raises=False)
    if rc != 0:
        ui.fatal("Could not get url of 'origin' remote:", out)
    return out


def get_project_name(repo_path):
    repo_url = get_project_url(repo_path)
    return project_name_from_url(repo_url)


def get_service(url):
    if "github" in url:
        return "github"
    else:
        return "gitlab"


def project_name_from_url(url):
    """
    >>> project_name_from_url('git@example.com:foo/bar.git')
    'foo/bar'
    >>> project_name_from_url('ssh://git@example.com:8022/foo/bar.git')
    'foo/bar'
    """
    # split everthing that is separated by a colon or a slash
    parts = re.split("[:/]", url)
    # join the last two parts
    res = "/".join(parts[-2:])
    # remove last `.git`
    if res.endswith(".git"):
        res = res[:-4]
    return res


def wipify(title):
    if not title.startswith(WIP_PREFIX):
        return WIP_PREFIX + title


def unwipify(title):
    if title.startswith(WIP_PREFIX):
        return title[len(WIP_PREFIX):]


class MRHandler(metaclass=abc.ABCMeta):
    def __init__(self, project_name, source_branch, target_branch):
        self.project_name = project_name
        self.source_branch = source_branch
        self.target_branch = target_branch

    @abc.abstractmethod
    def find_merge_request(self):
        pass

    @abc.abstractmethod
    def create_merge_request(self):
        pass

    @abc.abstractmethod
    def update_merge_request(self, merge_request, *, assignee, title, target_branch):
        pass

    @abc.abstractmethod
    def accept_merge_request(self, merge_request):
        pass

    @abc.abstractmethod
    def get_assignee(self):
        pass

    @abc.abstractmethod
    def get_project_id(self):
        pass

    # pylint: disable=no-self-use
    def handle_title(self, merge_request, args):
        # If set from command line: use it
        if args.mr_title:
            return args.mr_title
        else:
            # Change the title if we need to
            title = merge_request["title"]
            if args.ready:
                return unwipify(title)
            if args.wip:
                return wipify(title)

    def ensure_merge_request(self):
        merge_request = self.find_merge_request()
        if merge_request:
            ui.info_2("Found existing merge request: !%s" % merge_request["iid"])
            return merge_request
        else:
            ui.info_2("Creating new merge_request")
            return self.create_merge_request()

    def handle(self, args):
        assignee = self.get_assignee(args)

        merge_request = self.ensure_merge_request()

        title = self.handle_title(merge_request, args)

        self.update_merge_request(merge_request,
                                  title=title,
                                  target_branch=self.target_branch,
                                  assignee=assignee)

        if args.accept:
            self.accept_merge_request(merge_request)

        ui.info(ui.green, "::",
                ui.reset, "See merge request at", merge_request["web_url"])


class GitLabMRHandler(MRHandler):
    def __init__(self, project_name, source_branch, target_branch):
        super().__init__(project_name, source_branch, target_branch)
        self.api = None
        self._project_id = None

    @property
    def project_id(self):
        if not self._project_id:
            self._project_id = self.api.get_project_id(self.project_name)
        return self._project_id

    def get_assignee(self, args):
        pattern = args.assignee
        if not pattern:
            return None

        users = self.api.get_active_users()

        def sanitize(string):
            string = unidecode.unidecode(string)
            string = string.lower()
            return string

        # Sanitize both the list of names and the input
        usernames = [x["name"] for x in users]
        sanitized_names = [sanitize(x) for x in usernames]
        sanitized_pattern = sanitize(pattern)
        matches = list()
        for user, sanitized_name in zip(users, sanitized_names):
            if sanitized_pattern in sanitized_name:
                matches.append(user)
        if not matches:
            message = ui.did_you_mean("No user found matching %s" % pattern,
                                      pattern, usernames)

            raise tsrc.Error(message)
        if len(matches) > 1:
            ambiguous_names = [x["name"] for x in matches]
            raise tsrc.Error("Found several users matching %s: %s" %
                             (pattern, ", ".join(ambiguous_names)))

        if len(matches) == 1:
            return matches[0]

    def get_project_id(self):
        return self.api.get_project_id(self.project_name)

    def find_merge_request(self):
        return self.api.find_opened_merge_request(
            self.project_id, self.source_branch
        )

    def create_merge_request(self):
        return self.api.create_merge_request(
            self.project_id, self.source_branch,
            title=self.source_branch,
            target_branch=self.target_branch
        )

    def update_merge_request(self, merge_request, *, assignee, title, target_branch):
        params = {
            "title": title,
            "target_branch": target_branch,
            "remove_source_branch": True,
        }
        if assignee:
            params["assignee_id"] = assignee["id"]
        self.api.update_merge_request(merge_request, **params)

    def accept_merge_request(self, merge_request):
        self.api.accept_merge_request(merge_request)


class GitHubMRHandler(MRHandler):
    def __init__(self, project_name, source_branch, target_branch):
        super().__init__(project_name, source_branch, target_branch)
        self.api = None

    def find_merge_request(self):
        pass

    def create_merge_request(self):
        pass

    def accept_merge_request(self, merge_request):
        pass

    def update_merge_request(self, merge_request, *, assignee, title, target_branch):
        pass

    def get_assignee(self):
        pass

    def get_project_id(self):
        pass


def git_push(repo_path, source_branch, *, force=False):
    ui.info_2("Running git push")
    cmd = ["push", "-u", "origin", "%s:%s" % (source_branch, source_branch)]
    if force:
        cmd.append("--force")
    tsrc.git.run_git(repo_path, *cmd)


def main(args):
    repo_path = tsrc.git.get_repo_root()
    project_url = get_project_url(repo_path)
    project_name = get_project_name(project_url)
    service = get_service(project_url)
    current_branch = tsrc.git.get_current_branch(repo_path)
    source_branch = current_branch

    git_push(repo_path, source_branch, force=args.force)

    target_branch = args.target_branch
    if service == "github":
        handler = GitHubMRHandler(project_name, source_branch, target_branch)
        github_api = tsrc.github.login()
        handler.api = github_api
    else:
        handler = GitLabMRHandler(project_name, source_branch, target_branch)
        workspace = tsrc.cli.get_workspace(args)
        workspace.load_manifest()
        gitlab_url = workspace.get_gitlab_url()
        gitlab_token = get_gitlab_token()
        handler.api = tsrc.gitlab.GitLabHelper(gitlab_url, gitlab_token)
    handler.handle(args)
