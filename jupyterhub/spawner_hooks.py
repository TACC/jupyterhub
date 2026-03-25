import ast
import json
import os
import re
import time

import humanfriendly
import jwt
import requests
from tornado import web

from jupyterhub.common import (
    INSTANCE,
    TENANT,
    get_tenant_configs,
    get_user_configs,
    projects_url,
    refresh_access_token,
    safe_string,
    save_token,
    tapis_service_token,
)

# TAS configuration:
# base URL for TAS API.
TAS_URL_BASE = os.environ.get("TAS_URL_BASE", "https://tas.tacc.utexas.edu/api/v1")
TAS_ROLE_ACCT = os.environ.get("TAS_ROLE_ACCT", "tas-jetstream")
TAS_ROLE_PASS = os.environ.get("TAS_ROLE_PASS")


def hook(spawner):
    spawner.start_timeout = 60 * 5
    spawner.log.info(f"👻 tenant configs 👻 {spawner.configs}")
    spawner.log.info(f"👽 user configs 👽 {spawner.user_configs}")
    spawner.log.info(f"😱 user options (from form) 😱 {spawner.user_options}")

    get_tapis_access_data(spawner)
    spawner.log.info(
        f"access token: {spawner.access_token}, refresh token: {spawner.refresh_token}, url: {spawner.url}"
    )
    get_tas_data(spawner)

    if not spawner.tas_uid or not spawner.tas_gid:
        raise web.HTTPError(403)

    spawner.uid = int(spawner.tas_uid)
    spawner.gid = int(spawner.tas_gid)

    spawner.extra_pod_config = spawner.configs.get("extra_pod_config", {})
    spawner.extra_container_config = spawner.configs.get("extra_container_config", {})

    # for user_conf in spawner.user_configs:
    #     if "extra_pod_config" in user_conf["value"]:
    #         merge_configs(
    #             user_conf["value"]["extra_pod_config"], spawner.extra_pod_config
    #         )

    if (
        len(spawner.configs.get("images")) == 1 and not spawner.hpc_available
    ):  # only 1 image option, so we skipped the form
        spawner.image = spawner.configs.get("images")[0]["name"]
    else:
        # verify form data
        image_options = spawner.configs.get("images")
        for item in spawner.user_configs:
            for image in item["value"]["images"]:
                image_options.append(image)

        image = ast.literal_eval(spawner.user_options["image"][0])
        try:
            spawner.log.info(
                f"Checking user options: image-{image} hpc-{spawner.user_options.get('hpc')} against metadata: {image_options}"
            )
            allowed_options = next(
                option
                for option in image_options
                if option["name"] == image["name"]
                and option["display_name"] == image["display_name"]
            )
            if spawner.user_options.get("hpc"):
                if not eval(allowed_options.get("hpc_available", "False")):
                    spawner.log.error(
                        f"hpc is not available for this image. {spawner.user.name} -- {allowed_options}"
                    )
                    raise web.HTTPError(403)
        except Exception as e:
            spawner.log.error(
                f"{spawner.user.name} user options not allowed. selected options {spawner.user_options}. allowed options {image_options}. got an error:{e}"
            )
            raise web.HTTPError(403)

        spawner.image = image["name"]
        if image.get("extra_pod_config"):
            merge_configs(image["extra_pod_config"], spawner.extra_pod_config)
        if image.get("extra_container_config"):
            merge_configs(image["extra_container_config"], spawner.extra_pod_config)
        spawner.notebook_dir = image.get("notebook_dir", "")

    if not spawner.user_options.get("hpc"):
        # find highest available limit between tenant/user/group configs
        tenant_mem_limit = spawner.configs.get("mem_limit")
        mem_limits = {tenant_mem_limit: humanfriendly.parse_size(tenant_mem_limit)}
        cpu_limits = [spawner.configs.get("cpu_limit")]
        for item in spawner.user_configs:
            mem_limit = item["value"].get("mem_limit")
            cpu_limit = item["value"].get("cpu_limit")
            if mem_limit:
                mem_limits.update({mem_limit: humanfriendly.parse_size(mem_limit)})
            if cpu_limit:
                cpu_limits.append(cpu_limit)
        spawner.log.info(f"available limits -- mem: {mem_limits} cpu:{cpu_limits}")
        spawner.mem_limit = max(mem_limits, key=mem_limits.get)
        spawner.cpu_limit = float(max(cpu_limits))

        user = spawner.user.name
        uid = str(spawner.uid)
        gid = str(spawner.gid)

        # Set the guarantees really low because when None or 0,it sets a resource request for an amount equal to the limit
        spawner.mem_guarantee = ".001K"
        spawner.cpu_guarantee = float(0.001)
        spawner.cmty_writers = spawner.configs.get("cmty_writers", [])
        spawner.environment = {
            "MKL_NUM_THREADS": max(cpu_limits),
            "NUMEXPR_NUM_THREADS": max(cpu_limits),
            "OMP_NUM_THREADS": max(cpu_limits),
            "OPENBLAS_NUM_THREADS": max(cpu_limits),
            "SCINCO_JUPYTERHUB_IMAGE": spawner.image,
            "MLM_LICENSE_FILE": spawner.configs.get("mlm_license_file", ""),
            "HUB_USER": user,
            "HUB_UID": uid,
            "HUB_GID": gid,
        }
    print(f"Spawner environment: {spawner.environment}")
    get_mounts(spawner)
    # get_projects(spawner)
    get_licenses(spawner)


def merge_configs(x, y):
    merged_pod_config = {**x, **y}
    for key, value in merged_pod_config.items():
        if key in x and key in y:
            merged_pod_config[key].update(x[key])


async def parse_form_data(formdata, spawner):
    spawner.log.info(f"FORM DATA: {formdata}")
    return formdata


async def get_notebook_options(spawner):
    spawner.configs = await get_tenant_configs()
    spawner.user_configs = await get_user_configs(spawner.user.name)

    image_options = spawner.configs.get("images")

    for item in spawner.user_configs:
        for image in item["value"].get("images"):
            if image not in image_options:
                image_options += [image]
            if eval(image.get("hpc_available", "False")):
                spawner.hpc_available = True

    if not hasattr(
        spawner, "hpc_available"
    ):  # only looped through user options -- check the tenant options for hpc
        for image in spawner.configs.get("images"):
            if eval(image.get("hpc_available", "False")):
                spawner.hpc_available = True
                break
            spawner.hpc_available = False

    image_options = sorted(image_options, key=lambda d: d["name"])

    if len(image_options) > 1 or spawner.hpc_available:
        options = ""
        for image in image_options:
            options = (
                options
                + f" <option value='{json.dumps(image)}'> {image.get('display_name', image['name'])} </option>"
            )
        if spawner.hpc_available:
            hpc = """<input type="checkbox" id="hpc" name="hpc" style="display: none">
                <label for="hpc" id="hpc_label" style="display: none">Run on HPC</label>
                """
            js = """(function hpc(){
                var select_element = document.getElementById('image');
                var value = select_element.value || select_element.options[select_element.selectedIndex].value;
                var value = JSON.parse(value);
                document.getElementById('image_description').innerText = ''
                document.getElementsByClassName('btn-jupyter')[0].disabled = false;
                if ('description' in value) {
                    document.getElementById('image_description').innerText = value['description'];
                }
                if (value['hpc_available']) {
                    document.getElementById('hpc').checked = false;
                    document.getElementById('hpc').style.display = 'inline-block';
                    document.getElementById('hpc_label').style.display = 'inline-block';
                } else {
                    document.getElementById('hpc').checked = false;
                    document.getElementById('hpc').style.display = 'none';
                    document.getElementById('hpc_label').style.display = 'none';
                }
            })()"""
        else:
            js = """(function hpc(){
                            var select_element = document.getElementById('image');
                            var value = select_element.value || select_element.options[select_element.selectedIndex].value;
                            var value = JSON.parse(value);
                            document.getElementById('image_description').innerText = ''
                            document.getElementsByClassName('btn-jupyter')[0].disabled = false;
                            if ('description' in value) {
                                document.getElementById('image_description').innerText = value['description'];
                            }
                        })()"""

            hpc = ""

        image_description = (
            '<p id="image_description" style="display: inline-block"> </p>'
        )
        select_images = '<select id="image" name="image" size="10" onchange="{}"> {} </select>'.format(
            js, options
        )
        return f"{select_images}{image_description}{hpc}"


def get_tapis_access_data(spawner):
    """
    Returns the access token and base URL cached in the agavepy file
    :return:
    """
    # TODO figure out naming conventions that can follow k8 rules
    # k8 names must consist of lower case alphanumeric characters, '-' or '.',
    # and must start and end with an alphanumeric character
    # do all tenant names follow that? usernames?
    token_file = os.path.join(get_user_token_dir(spawner.user.name), ".tapipy")
    spawner.log.info(
        f"spawner looking for token file: {token_file} for user: {spawner.user.name}"
    )
    if not os.path.exists(token_file):
        spawner.log.warning(f"spawner did not find a token file at {token_file}")
        return None
    try:
        data = json.load(open(token_file))
    except ValueError:
        spawner.log.warning("could not ready json from token file")
        return None

    try:
        spawner.access_token = data[0]["token"]
        try:
            decoded_data = jwt.decode(
                data[0]["token"], options={"verify_signature": False}
            )
        except Exception as e:
            print(f"Error decoding access token: {e}")

        refresh_data = None
        if "exp" in decoded_data and decoded_data["exp"] < time.time():
            spawner.log.info(
                f"{spawner.user.name} has expired access token, attempting to refresh"
            )
            refresh_data = refresh_access_token(
                data[0]["refresh_token"], spawner.user.name
            )
            spawner.log.info(f"Data retrieved from refreshing: {refresh_data}")

        if refresh_data:
            spawner.log.info(
                f"Refreshed access token for: {spawner.user.name}, attempting to save and update tapipy files"
            )
            save_token(
                refresh_data["access_token"],
                refresh_data["refresh_token"],
                spawner.user.name,
                refresh_data["created_at"],
                refresh_data["expires_in"],
                refresh_data["expires_at"],
            )
            spawner.access_token = refresh_data["access_token"]
            spawner.log.info(f"Setting token: {spawner.access_token}")
            spawner.refresh_token = refresh_data["refresh_token"]
            spawner.log.info(f"Setting refresh token: {spawner.refresh_token}")
        else:
            spawner.log.info(f"Setting token: {spawner.access_token}")
            spawner.refresh_token = data[0]["refresh_token"]
            spawner.log.info(f"Setting refresh token: {spawner.refresh_token}")

        spawner.url = data[0]["api_server"]
        spawner.log.info(f"Setting url: {spawner.url}")

    except (TypeError, KeyError):
        spawner.log.warning(
            f"token file did not have an access token and/or an api_server. data: {data}"
        )
        return None


def get_tas_data(spawner):
    """Get the TACC uid, gid and homedir for this user from the TAS API."""
    if not TAS_ROLE_ACCT:
        spawner.log.error("No TAS_ROLE_ACCT configured. Aborting.")
        return
    if not TAS_ROLE_PASS:
        spawner.log.error("No TAS_ROLE_PASS configured. Aborting.")
        return
    url = f"{TAS_URL_BASE}/users/username/{spawner.user.name}"
    headers = {"Content-type": "application/json", "Accept": "application/json"}
    try:
        rsp = requests.get(
            url,
            headers=headers,
            auth=requests.auth.HTTPBasicAuth(TAS_ROLE_ACCT, TAS_ROLE_PASS),
        )
    except Exception as e:
        spawner.log.error(
            f"Got an exception from TAS API. \nException: {e}. url: {url}. TAS_ROLE_ACCT: {TAS_ROLE_ACCT}"
        )
        return
    try:
        data = rsp.json()
    except Exception as e:
        spawner.log.error(
            f"Did not get JSON from TAS API. rsp: {rsp} \nException: {e}. url: {url}. TAS_ROLE_ACCT: {TAS_ROLE_ACCT}"
        )
        return
    spawner.tas_gid = None
    try:
        spawner.tas_uid = data["result"]["uid"]
        spawner.tas_gid = data["result"]["gid"]
        spawner.tas_homedir = data["result"]["homeDirectory"]
    except Exception as e:
        spawner.log.error(
            f"Did not get attributes from TAS API. rsp: {rsp} \nException: {e}. url: {url}. TAS_ROLE_ACCT: {TAS_ROLE_ACCT}"
        )
        return

    spawner.log.info(
        f"Setting the following TAS data: uid:{spawner.tas_uid} gid:{spawner.tas_gid} homedir:{spawner.tas_homedir}"
    )


def get_user_token_dir(username):
    return os.path.join("/tapis/jupyter/tokens", INSTANCE, TENANT, username)


def get_mounts(spawner):
    safe_username = safe_string(spawner.user.name).lower()
    safe_tenant = safe_string(TENANT).lower()
    safe_instance = safe_string(INSTANCE).lower()
    tapipy_safe_name = f"{safe_username}-{safe_tenant}-{safe_instance}-jhub-tapipy"
    current_safe_name = f"{safe_username}-{safe_tenant}-{safe_instance}-jhub-current"

    spawner.init_containers = [
        {
            "name": "rw-configmap-workaround",
            "image": "busybox",
            "command": [
                "/bin/sh",
                "-c",
                "cp -r /tapis_data/.tapipy /tapis_data_rw/.tapipy && cp -r /tapis_data/current /tapis_data_rw/current && ls -lah /tapis_data_rw && cat /tapis_data_rw/current/current && chmod -R 777 /tapis_data_rw && ls -lah /tapis_data_rw",
            ],
            "volumeMounts": [
                {
                    "mountPath": "/tapis_data/.tapipy",
                    "name": f"{tapipy_safe_name}-configmap",
                    "subPath": ".tapipy",
                },
                {
                    "mountPath": "/tapis_data/current",
                    "name": f"{current_safe_name}-configmap",
                    "subPath": "current",
                },
                {
                    "mountPath": "/tapis_data_rw/.tapipy",
                    "name": tapipy_safe_name,
                    "subPath": ".tapipy",
                },
                {
                    "mountPath": "/tapis_data_rw/current",
                    "name": current_safe_name,
                    "subPath": "current",
                },
            ],
        }
    ]

    spawner.volumes = [
        {
            "name": f"{tapipy_safe_name}-configmap",
            "configMap": {"name": tapipy_safe_name, "defaultMode": 0o0777},
        },
        {
            "name": f"{current_safe_name}-configmap",
            "configMap": {"name": current_safe_name, "defaultMode": 0o0777},
        },
        {
            "name": tapipy_safe_name,
            "emptyDir": {},
        },
        {
            "name": current_safe_name,
            "emptyDir": {},
        },
        # {
        #     "name": "extrausers",
        #     "configMap": {"name": f"{safe_username}-passwd", "defaultMode": 0o0444},
        # }
    ]
    spawner.volume_mounts = [
        {
            "mountPath": "/etc/.tapipy",
            "name": tapipy_safe_name,
            "subPath": ".tapipy/.tapipy",
        },
        {
            "mountPath": "/home/jupyter/.tapis-token",
            "name": current_safe_name,
            "subPath": "current",
        },
        # {
        #     "name": "extrausers",
        #     "mountPath": "/var/lib/extrausers/passwd",
        #     "readOnly": "true"
        # }
    ]
    volume_mounts = spawner.configs.get("volume_mounts")

    for item in spawner.user_configs:
        if item["value"].get("volume_mounts"):
            volume_mounts += [
                x for x in item["value"]["volume_mounts"] if x not in volume_mounts
            ]

    template_vars = {
        "username": spawner.user.name,
        "tenant_id": TENANT,  # TODO do we need this?
    }

    if hasattr(spawner, "tas_homedir"):
        template_vars["tas_homedir"] = spawner.tas_homedir

    if len(volume_mounts):
        for item in volume_mounts:
            path = item["path"].format(**template_vars)

            # volume names must consist of lower case alphanumeric characters or '-',
            # and must start and end with an alphanumeric character (e.g. 'my-name',  or '123-abc',
            # regex used for validation is '[a-z0-9]([-a-z0-9]*[a-z0-9])?')
            if item["mountPath"][-1] == "/":
                item["mountPath"] = item["mountPath"][:-1]
            vol_name = re.sub(
                r"([^a-z0-9-\s]+?)", "", item["mountPath"].split("/")[-1].lower()
            )

            vol = {"path": path, "readOnly": eval(item["readOnly"])}
            if item["type"] == "nfs":
                vol["server"] = item["server"]

            spawner.volumes.append({"name": vol_name, item["type"]: vol})

            spawner.volume_mounts.append(
                {"mountPath": item["mountPath"], "name": vol_name}
            )
        spawner.log.info(f"volumes: {spawner.volumes}")
        spawner.log.info(f"volume_mounts: {spawner.volume_mounts}")


def get_projects(spawner):
    spawner.host_projects_root_dir = spawner.configs.get("host_projects_root_dir")
    spawner.container_projects_root_dir = spawner.configs.get(
        "container_projects_root_dir"
    )
    spawner.network_storage = spawner.configs.get("network_storage")
    spawner.jupyterh_bearer_token = spawner.configs.get("jupyterh_bearer_token")

    spawner.log.info(f"Access token: {spawner.access_token}")

    if not spawner.host_projects_root_dir or not spawner.container_projects_root_dir:
        spawner.log.info(
            f"No host_projects_root_dir or container_projects_root_dir. configs:{spawner.configs}"
        )
        return None
    if not spawner.access_token or not spawner.url:
        spawner.log.info("no access_token or url")
        return None

    try:
        headers = {"x-tapis-token": spawner.access_token}

        url = f"{projects_url}/api/projects/v2"

        rsp = requests.get(url, headers=headers)

        data = rsp.json()

        projects = data.get("result")
        spawner.log.info(f"service returned projects: {projects}")
    except Exception as e:
        spawner.log.warn(
            f"Got exception calling /projects for user: {spawner.user.name}; error: {e}"
        )
        return None

    try:
        spawner.log.info(f"Found {len(projects)} projects")
    except TypeError:
        spawner.log.error("Projects data has no length.")
        spawner.log.info(f"response: {rsp}, data: {data}")
        return None

    for project in projects:
        uuid = project.get("uuid")
        if not uuid:
            spawner.log.warn(f"Did not get a uuid for project: {project}")
            continue
        project_id = project.get("value").get("projectId")
        if not project_id:
            spawner.log.warn(f"Did not get a projectId for project: {project}")
            continue

        server = spawner.network_storage
        mountPath = f"{spawner.container_projects_root_dir}/{project_id}"
        # if uuid == "7997906542076432871-242ac11c-0001-012":
        #     if spawner.user.name in spawner.cmty_writers:
        #         for vol in spawner.volumes:
        #             if vol['name'] == 'communitydata':
        #                 del vol
                        # vol['nfs']['readOnly'] = False
            # path = "/corral/main/projects/NHERI/community"

        path = f"{spawner.host_projects_root_dir}/{uuid}"

        spawner.volumes.append(
            {
                "name": f"project-{safe_string(uuid).lower()}",
                "nfs": {
                    "server": server,
                    "path": path,
                    "readOnly": False,
                },
            }
        )

        spawner.volume_mounts.append(
            {"mountPath": mountPath, "name": f"project-{safe_string(uuid).lower()}"}
        )

    spawner.log.info(spawner.volumes)
    spawner.log.info(spawner.volume_mounts)


def get_licenses(spawner):
    if not spawner.access_token:
        spawner.log.info("no access_token")
        return

    matlab_url = f"{projects_url}/api/licenses/MATLAB/?username={spawner.user.name}"
    lsdyna_url = f"{projects_url}/api/licenses/LSDYNA/?username={spawner.user.name}"

    spawner.log.warn(projects_url)
    spawner.log.warn(matlab_url)
    spawner.log.warn(lsdyna_url)

    headers = {"x-tapis-token": tapis_service_token}
    spawner.log.warn(headers)

    try:
        rsp = requests.get(matlab_url, headers=headers)

        data = rsp.json()
        spawner.log.warn(data)
        spawner.environment["MATLAB_LICENSE"] = data["license"]
    except Exception as e:
        spawner.log.warn(
            f"Got exception calling MATLAB license for user: {spawner.user.name}; error: {e}"
        )

    try:
        rsp = requests.get(lsdyna_url, headers=headers)

        data = rsp.json()
        spawner.log.warn(data)
        spawner.environment["LSDYNA_LICENSE"] = data["license"]
    except Exception as e:
        spawner.log.warn(
            f"Got exception calling LSDYNA license for user: {spawner.user.name}; error: {e}"
        )
