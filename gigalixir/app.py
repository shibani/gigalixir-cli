import os
import pipes
import logging
import urllib
import json
import subprocess
import requests
import click
from .shell import cast, call
from . import auth
from . import presenter
from . import ssh_key
from contextlib import closing
from six.moves.urllib.parse import quote

def get(host):
    r = requests.get('%s/api/apps' % host, headers = {
        'Content-Type': 'application/json',
    })
    if r.status_code != 200:
        if r.status_code == 401:
            raise auth.AuthException()
        raise Exception(r.text)
    else:
        data = json.loads(r.text)["data"]
        presenter.echo_json(data)

def set_git_remote(host, app_name):
    remotes = call('git remote').splitlines()
    if 'gigalixir' in remotes:
        cast('git remote rm gigalixir')
    cast('git remote add gigalixir https://git.gigalixir.com/%s.git/' % app_name)
    logging.getLogger("gigalixir-cli").info("Set git remote: gigalixir.")

def create(host, unique_name, cloud, region):
    try:
        # check for git folder
        with open(os.devnull, 'w') as FNULL:
            subprocess.check_call('git rev-parse --is-inside-git-dir'.split(), stdout=FNULL, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError:
        raise Exception("You must call this from inside a git repository.")

    body = {}
    if unique_name != None:
        body["unique_name"] = unique_name.lower()
    if cloud != None:
        body["cloud"] = cloud
    if region != None:
        body["region"] = region
    r = requests.post('%s/api/apps' % host, headers = {
        'Content-Type': 'application/json',
    }, json = body)
    if r.status_code != 201:
        if r.status_code == 401:
            raise auth.AuthException()
        raise Exception(r.text)
    else:
        data = json.loads(r.text)["data"]
        unique_name = data["unique_name"]
        logging.getLogger("gigalixir-cli").info("Created app: %s." % unique_name)

        set_git_remote(host, unique_name)
        click.echo(unique_name)

def status(host, app_name):
    r = requests.get('%s/api/apps/%s/status' % (host, quote(app_name.encode('utf-8'))), headers = {
        'Content-Type': 'application/json',
    })
    if r.status_code != 200:
        if r.status_code == 401:
            raise auth.AuthException()
        raise Exception(r.text)
    else:
        data = json.loads(r.text)["data"]
        presenter.echo_json(data)

def scale(host, app_name, replicas, size):
    json = {}
    if replicas != None:
        json["replicas"] = replicas
    if size != None:
        json["size"] = size 
    r = requests.put('%s/api/apps/%s/scale' % (host, quote(app_name.encode('utf-8'))), headers = {
        'Content-Type': 'application/json',
    }, json = json)
    if r.status_code != 200:
        if r.status_code == 401:
            raise auth.AuthException()
        raise Exception(r.text)

def customer_app_name(host, app_name):
    r = requests.get('%s/api/apps/%s/releases/latest' % (host, quote(app_name.encode('utf-8'))), headers = {
        'Content-Type': 'application/json',
    })
    if r.status_code != 200:
        if r.status_code == 401:
            raise auth.AuthException()
        raise Exception(r.text)
    else:
        data = json.loads(r.text)["data"]
        return data["customer_app_name"]

def distillery_eval(host, app_name, ssh_opts, expression):
    # capture_output == True as this isn't interactive
    # and we want to return the result as a string rather than
    # print it out to the screen
    return ssh_helper(host, app_name, ssh_opts, True, "gigalixir_run", "distillery_eval", "--", expression)

def distillery_command(host, app_name, ssh_opts, *args):
    ssh(host, app_name, ssh_opts, "gigalixir_run", "shell", "--", "bin/%s" % customer_app_name(host, app_name), *args)

def ssh(host, app_name, ssh_opts, *args):
    # capture_output == False for interactive mode which is
    # used by ssh, remote_console, distillery_command
    ssh_helper(host, app_name, ssh_opts, False, *args)

# if using this from a script, and you want the return
# value in a variable, use capture_output=True
# capture_output needs to be False for remote_console
# and regular ssh to work.
def ssh_helper(host, app_name, ssh_opts, capture_output, *args):
    # verify SSH keys exist
    keys = ssh_key.ssh_keys(host)
    if len(keys) == 0:
        raise Exception("You don't have any ssh keys yet. See `gigalixir add_ssh_key --help`")

    r = requests.get('%s/api/apps/%s/ssh_ip' % (host, quote(app_name.encode('utf-8'))), headers = {
        'Content-Type': 'application/json',
    })
    if r.status_code != 200:
        if r.status_code == 401:
            raise auth.AuthException()
        raise Exception(r.text)
    else:
        data = json.loads(r.text)["data"]
        ssh_ip = data["ssh_ip"]
        if len(args) > 0:
            escaped_args = [pipes.quote(arg) for arg in args]
            command = " ".join(escaped_args)
            if capture_output:
                return call("ssh %s -t root@%s %s" % (ssh_opts, ssh_ip, command))
            else:
                cast("ssh %s -t root@%s %s" % (ssh_opts, ssh_ip, command))
        else:
            cast("ssh %s -t root@%s" % (ssh_opts, ssh_ip))


def restart(host, app_name):
    r = requests.put('%s/api/apps/%s/restart' % (host, quote(app_name.encode('utf-8'))), headers = {
        'Content-Type': 'application/json',
    })
    if r.status_code != 200:
        if r.status_code == 401:
            raise auth.AuthException()
        raise Exception(r.text)

def rollback(host, app_name, version):
    if version == None:
        version = second_most_recent_version(host, app_name)
    r = requests.post('%s/api/apps/%s/releases/%s/rollback' % (host, quote(app_name.encode('utf-8')), quote(str(version).encode('utf-8'))), headers = {
        'Content-Type': 'application/json',
    })
    if r.status_code != 200:
        if r.status_code == 401:
            raise auth.AuthException()
        raise Exception(r.text)

def second_most_recent_version(host, app_name):
    r = requests.get('%s/api/apps/%s/releases' % (host, quote(app_name.encode('utf-8'))), headers = {
        'Content-Type': 'application/json',
    })
    if r.status_code != 200:
        if r.status_code == 401:
            raise auth.AuthException()
        raise Exception(r.text)
    else:
        data = json.loads(r.text)["data"]
        if len(data) < 2:
            raise Exception("No release available to rollback to.")
        else:
            return data[1]["version"]

def run(host, app_name, command):
    # runs command in a new container
    r = requests.post('%s/api/apps/%s/run' % (host, quote(app_name.encode('utf-8'))), headers = {
        'Content-Type': 'application/json',
    }, json = {
        "command": command,
    })
    if r.status_code != 200:
        if r.status_code == 401:
            raise auth.AuthException()
        raise Exception(r.text)
    else:
        click.echo("Starting new container to run: `%s`." % ' '.join(command))
        click.echo("See `gigalixir logs %s` for any output." % app_name)
        click.echo("See `gigalixir status %s` for job info." % app_name)

def ps_run(host, app_name, ssh_opts, *command):
    # runs command in same container app is running
    ssh(host, app_name, ssh_opts, "gigalixir_run", "shell", "--", *command)

def remote_console(host, app_name, ssh_opts):
    ssh(host, app_name, ssh_opts, "gigalixir_run", "remote_console")

def migrate(host, app_name, migration_app_name, ssh_opts):
    if migration_app_name == None:
        r = requests.get('%s/api/apps/%s/migrate-command' % (host, quote(app_name.encode('utf-8'))), headers = {
            'Content-Type': 'application/json',
        })
    else:
        r = requests.get('%s/api/apps/%s/migrate-command?migration_app_name=%s' % (host, quote(app_name.encode('utf-8')), quote(migration_app_name.encode('utf-8'))), headers = {
            'Content-Type': 'application/json',
        })
    if r.status_code != 200:
        if r.status_code == 401:
            raise auth.AuthException()
        raise Exception(r.text)
    else:
        command = json.loads(r.text)["data"]
        try:
            result = distillery_eval(host, app_name, ssh_opts, command)
            click.echo("Migration succeeded.")
            click.echo("Migrations run: %s" % result)
        except subprocess.CalledProcessError as e:
            # tell the user why it failed
            click.echo(e.output)
            raise

def logs(host, app_name, num, no_tail):
    payload = {
        "num_lines": num,
        "follow": not no_tail
    }
    with closing(requests.get('%s/api/apps/%s/logs' % (host, quote(app_name.encode('utf-8'))), stream=True, params=payload)) as r:
        if r.status_code != 200:
            if r.status_code == 401:
                raise auth.AuthException()
            raise Exception(r.text)
        else:
            for chunk in r.iter_content(chunk_size=None):
                if chunk:
                    click.echo(chunk, nl=False)

def delete(host, app_name):
    r = requests.delete('%s/api/apps/%s' % (host, quote(app_name.encode('utf-8'))), headers = {
        'Content-Type': 'application/json',
    })
    if r.status_code != 200:
        if r.status_code == 401:
            raise auth.AuthException()
        raise Exception(r.text)

