"""
Copyright 2019-2020 Amazon.com, Inc. or its affiliates. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License"). You
may not use this file except in compliance with the License. A copy of
the License is located at

    http://aws.amazon.com/apache2.0/

or in the "license" file accompanying this file. This file is
distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
ANY KIND, either express or implied. See the License for the specific
language governing permissions and limitations under the License.
"""
import os
import re
import json
import logging
import sys
import boto3
import constants

from config import is_build_enabled
from invoke.context import Context
from botocore.exceptions import ClientError
from safety_report_generator import SafetyReportGenerator

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)
LOGGER.addHandler(logging.StreamHandler(sys.stdout))
LOGGER.addHandler(logging.StreamHandler(sys.stderr))


def get_codebuild_build_arn():
    """
    Get env variable CODEBUILD_BUILD_ARN

    @return: value or empty string if not set
    """
    return os.getenv("CODEBUILD_BUILD_ARN", "")


class JobParameters:
    image_types = []
    device_types = []
    py_versions = []
    image_run_test_types = {}

    @staticmethod
    def build_for_all_images():
        JobParameters.image_types = constants.ALL
        JobParameters.device_types = constants.ALL
        JobParameters.py_versions = constants.ALL

    @staticmethod
    def add_image_types(value):
        if JobParameters.image_types != constants.ALL:
            JobParameters.image_types.append(value)

    @staticmethod
    def build_for_all_device_types_py_versions():
        JobParameters.device_types = constants.ALL
        JobParameters.py_versions = constants.ALL

    @staticmethod
    def do_build_all_images():
        return (
            JobParameters.device_types == constants.ALL
            and JobParameters.image_types == constants.ALL
            and JobParameters.py_versions == constants.ALL
        )

def download_s3_file(bucket_name, filepath, local_file_name):
    """

    :param bucket_name: string
    :param filepath: string
    :param local_file_name: string
    :return:
    """
    _s3 = boto3.Session().resource('s3')

    try:
        _s3.Bucket(bucket_name).download_file(filepath, local_file_name)
    except ClientError as e:
        LOGGER.error("Error: Cannot read file from s3 bucket.")
        LOGGER.error("Exception: {}".format(e))
        raise

def download_file(remote_url: str, link_type: str):
    """
    Fetch remote files and save with provided local_path name
    :param link_type: string
    :param remote_url: string
    :return: file_name: string
    """
    LOGGER.info(f"Downloading {remote_url}")

    file_name = os.path.basename(remote_url).strip()
    LOGGER.info(f"basename: {file_name}")

    if link_type in ["s3"] and remote_url.startswith("s3://"):
        match = re.match(r's3:\/\/(.+?)\/(.+)', remote_url)
        if match:
            bucket_name = match.group(1)
            bucket_key = match.group(2)
            LOGGER.info(f"bucket_name: {bucket_name}")
            LOGGER.info(f"bucket_key: {bucket_key}")
            download_s3_file(bucket_name, bucket_key, file_name)
        else:
            raise ValueError(f"Regex matching on s3 URI failed.")
    else:
        ctx = Context()
        ctx.run(f"curl -O {remote_url}")

    return file_name


def get_pr_modified_files(pr_number):
    """
    Fetch all the files modified for a git pull request and return them as a string
    :param pr_number: int
    :return: str with all the modified files
    """
    # This import statement has been placed inside this function because it creates a dependency that is unnecessary
    # for local builds and builds outside of Pull Requests.
    from dlc.github_handler import GitHubHandler

    # Example: "https://github.com/aws/deep-learning-containers.git"
    repo_url = os.getenv("CODEBUILD_SOURCE_REPO_URL")
    _, user, repo_name = repo_url.rstrip(".git").rsplit("/", 2)

    github_handler = GitHubHandler(user, repo_name)
    files = github_handler.get_pr_files_changed(pr_number)
    files = "\n".join(files)
    return files


def update_image_run_test_types(image_build_string, test_type):
    """
    Map the test_types with image_tags or job_type values, we use this mapping in fetch_dlc_images_for_test_jobs
    to append images for each test job
    :param image_build_string: str (image_name or training or inference or all)
    :param test_type: str (all or ec2 or ecs or eks or sagemaker)
    :return:
    """
    if image_build_string in JobParameters.image_run_test_types.keys():
        test = JobParameters.image_run_test_types.get(image_build_string)
        # If image_build_string is already present
        # we will only append the test_type if it doesn't have all tests.
        if constants.ALL not in test and test_type != constants.ALL:
            test.append(test_type)
            JobParameters.image_run_test_types[image_build_string] = test
        # if test_type is "all" then we override existing value with that.
        elif test_type == constants.ALL:
            JobParameters.image_run_test_types[image_build_string] = [test_type]
    # Assigning the test_type to image_build_string for the first time
    else:
        JobParameters.image_run_test_types[image_build_string] = [test_type]


def parse_modified_docker_files_info(files, framework, pattern=""):
    """
    Parse all the files in PR to find docker file related changes for any framework
    triggers an image build matching the image_type(training/testing), device_type(cpu_gpu)
    and python version(py2 and py3) of the changed docker files
    :param files: str
    :param framework: str
    :param pattern: str
    :return: None
    """
    rule = re.findall(rf"{pattern}", files)
    for dockerfile in rule:
        dockerfile = dockerfile.split("/")
        if dockerfile[0] == "huggingface":
            # HuggingFace related files stored in huggingface/<framework> directories
            # Joining 1 and 2 elements to get huggingface_<framework> as a first element
            dockerfile = [f"{dockerfile[0]}_{dockerfile[1]}"]+dockerfile[2:]
        framework_change = dockerfile[0]

        if dockerfile[0] == "habana":
            framework_change = dockerfile[1]
            dockerfile = [f"{dockerfile[0]}_{dockerfile[1]}"]+dockerfile[2:]
        # If the modified dockerfile belongs to a different
        # framework, do nothing
        if framework_change != framework:
            continue
        image_type = dockerfile[1]
        py_version = dockerfile[4]
        device_type = dockerfile[-1].split(".")[-1]
        LOGGER.info(f"Building dockerfile: {dockerfile}")
        # Use class static variables to avoid passing, returning the varibles from all functions
        JobParameters.device_types.append(device_type)
        JobParameters.image_types.append(image_type)
        JobParameters.py_versions.append(py_version)
        # create a map for the image_build_string and run_test_types on it
        # this map will be used to update the DLC_IMAGES for pr test jobs
        run_tests_key = f"{image_type}_{device_type}_{py_version}"
        update_image_run_test_types(run_tests_key, constants.ALL)


def parse_modifed_buidspec_yml_info(files, framework, pattern=""):
    """
    trigger a build for all the images related to a framework when there is change in framework/buildspec.yml
    :param files: str
    :param framework: str
    :param pattern: str
    :return: None
    """
    rule = re.findall(rf"{pattern}", files)
    for buildspec in rule:
        buildspec_arr = buildspec.split("/")
        if buildspec_arr[0] == "huggingface":
            # HuggingFace related files stored in huggingface/<framework> directories
            # Joining 1 and 2 elements to get huggingface_<framework> as a first element
            buildspec_arr = [f"{buildspec_arr[0]}_{buildspec_arr[1]}"]+buildspec_arr[2:]
        buildspec_framework = buildspec_arr[0]
        if buildspec_arr[0] == "habana":
            buildspec_framework = buildspec_arr[1]
        if buildspec_framework == framework:
            JobParameters.build_for_all_images()
            update_image_run_test_types(constants.ALL, constants.ALL)


# Rule 3: If any file in the build code changes, build all images
def parse_modifed_root_files_info(files, pattern=""):
    """
    trigger a build for all the images for all the frameworks when there is change in src, test, testspec.yml files
    :param files: str
    :param pattern: str
    :return: None
    """
    rule = re.findall(rf"{pattern}", files)
    if rule:
        JobParameters.build_for_all_images()
        update_image_run_test_types(constants.ALL, constants.ALL)


def parse_modified_sagemaker_test_files(files, framework, pattern=""):
    """
    Parse all the files in PR to find sagemaker tests related changes for any framework
    to trigger an image build matching the image_type(training/testing) for all the device_types(cpu,gpu)
    and python_versions(py2,py3)
    :param files: str
    :param framework: str
    :param pattern: str
    :return: None
    """
    rule = re.findall(rf"{pattern}", files)
    for test_file in rule:
        test_dirs = test_file.split("/")
        test_folder = test_dirs[0]
        if test_folder == "sagemaker_tests":
            framework_changed = test_dirs[1]
            # The below code looks for file changes in /test/sagemaker_tests/(mxnet|pytorch|tensorflow) directory
            if framework_changed == framework:
                job_name = test_dirs[2]
                # The training folder structure for tensorflow is tensorflow1_training(1.x), tensorflow2_training(2.x)
                # so we are stripping the tensorflow1 from the name
                if framework_changed == "tensorflow" and "training" in job_name:
                    job_name = "training"
                if job_name in constants.IMAGE_TYPES:
                    JobParameters.add_image_types(job_name)
                    JobParameters.build_for_all_device_types_py_versions()
                    update_image_run_test_types(job_name, constants.SAGEMAKER_TESTS)
                # If file changed is under /test/sagemaker_tests/(mxnet|pytorch|tensorflow)
                # but not in training/inference dirs
                else:
                    JobParameters.build_for_all_images()
                    update_image_run_test_types(
                        constants.ALL, constants.SAGEMAKER_TESTS
                    )
                    break
            # If file changed is under /test/sagemaker_tests but not in (mxnet|pytorch|tensorflow) dirs
            elif framework_changed not in constants.FRAMEWORKS:
                JobParameters.build_for_all_images()
                update_image_run_test_types(constants.ALL, constants.SAGEMAKER_TESTS)
                break


def parse_modified_dlc_test_files_info(files, framework, pattern=""):
    """
    Parse all the files in PR to find ecs/eks/ec2 tests related changes for any framework
    to trigger an image build matching the image_type(training/testing) for all the device_types(cpu,gpu)
    and python_versions(py2,py3)
    :param files:
    :param framework:
    :param pattern:
    :return: None
    """
    rule = re.findall(rf"{pattern}", files)
    # JobParameters variables are not set with constants.ALL
    for test_file in rule:
        test_dirs = test_file.split("/")
        test_folder = test_dirs[0]
        if test_folder == "dlc_tests":
            test_name = test_dirs[1]
            # The below code looks for file changes in /test/dlc_tests/(ecs|eks|ec2) directory
            if test_name in ["ecs", "eks", "ec2"]:
                framework_changed = test_dirs[2]
                if framework_changed == framework:
                    job_name = test_dirs[3]
                    if job_name in constants.IMAGE_TYPES:
                        JobParameters.add_image_types(job_name)
                        JobParameters.build_for_all_device_types_py_versions()
                        update_image_run_test_types(job_name, test_name)
                    # If file changed is under /test/dlc_tests/(ecs|eks|ec2)
                    # but not in (inference|training) dirs
                    else:
                        JobParameters.build_for_all_images()
                        update_image_run_test_types(constants.ALL, test_name)
                        break
                # If file changed is under /test/dlc_tests/(ecs|eks|ec2) dirs init and conftest files
                elif framework_changed not in constants.FRAMEWORKS:
                    JobParameters.build_for_all_images()
                    update_image_run_test_types(constants.ALL, test_name)
                    break
            # If file changed is under /test/dlc_tests/ dir sanity, container_tests dirs
            # and init, conftest files
            else:
                JobParameters.build_for_all_images()
                update_image_run_test_types(constants.ALL, constants.EC2_TESTS)
                update_image_run_test_types(constants.ALL, constants.ECS_TESTS)
                update_image_run_test_types(constants.ALL, constants.EKS_TESTS)
                break


def pr_build_setup(pr_number, framework):
    """
    Identify the PR changeset and set the appropriate environment
    variables
    Parameters:
        pr_number: int

    Returns:
        device_types: [str]
        image_types: [str]
        py_versions: [str]
    """
    files = get_pr_modified_files(pr_number)

    # This below code currently appends the values to device_types, image_types, py_versions for files changed
    # if there are no changes in the files then functions return same lists
    parse_modified_docker_files_info(files, framework, pattern="\S+Dockerfile\S+")

    parse_modified_sagemaker_test_files(
        files, framework, pattern="sagemaker_tests\/\S+"
    )

    # The below functions are only run if all JobParameters variables are not set with constants.ALL
    parse_modified_dlc_test_files_info(files, framework, pattern="dlc_tests\/\S+")

    # The below code currently overides the device_types, image_types, py_versions with constants.ALL
    # when there is a change in any the below files
    parse_modifed_buidspec_yml_info(files, framework, pattern="\S+\/buildspec.*yml")

    parse_modifed_root_files_info(files, pattern="src\/\S+")

    parse_modifed_root_files_info(
        files, pattern="(?:test\/(?!(dlc_tests|sagemaker_tests))\S+)"
    )

    parse_modifed_root_files_info(files, pattern="testspec\.yml")

    return (
        JobParameters.device_types,
        JobParameters.image_types,
        JobParameters.py_versions,
    )


def build_setup(framework, device_types=None, image_types=None, py_versions=None):
    """
    Setup the appropriate environment variables depending on whether this is a PR build
    or a dev build

    Parameters:
        framework: str
        device_types: [str]
        image_types: [str]
        py_versions: [str]

    Returns:
        None
    """

    # Set necessary environment variables
    to_build = {
        "device_types": constants.DEVICE_TYPES,
        "image_types": constants.IMAGE_TYPES,
        "py_versions": constants.PYTHON_VERSIONS,
    }
    build_context = os.environ.get("BUILD_CONTEXT")
    enable_build = is_build_enabled()

    if build_context == "PR":
        pr_number = os.getenv("PR_NUMBER")
        LOGGER.info(f"pr number: {pr_number}")
        if pr_number is not None:
            pr_number = int(pr_number)
        device_types, image_types, py_versions = pr_build_setup(pr_number, framework)

    if device_types != constants.ALL:
        to_build["device_types"] = constants.DEVICE_TYPES.intersection(
            set(device_types)
        )
    if image_types != constants.ALL:
        to_build["image_types"] = constants.IMAGE_TYPES.intersection(set(image_types))
    if py_versions != constants.ALL:
        to_build["py_versions"] = constants.PYTHON_VERSIONS.intersection(
            set(py_versions)
        )
    for device_type in to_build["device_types"]:
        for image_type in to_build["image_types"]:
            for py_version in to_build["py_versions"]:
                env_variable = f"{framework.upper()}_{device_type.upper()}_{image_type.upper()}_{py_version.upper()}"
                if enable_build or build_context != "PR":
                    os.environ[env_variable] = "true"


def fetch_dlc_images_for_test_jobs(images, use_latest_additional_tag=False):
    """
    use the JobParamters.run_test_types values to pass on image ecr urls to each test type.
    :param images: list
    :return: dictionary
    """
    # DLC_IMAGES = {"sagemaker": [], "ecs": [], "eks": [], "ec2": [], "sanity": []}
    DLC_IMAGES = {"sanity": []}

    build_enabled = is_build_enabled()

    for docker_image in images:
        if not docker_image.is_test_promotion_enabled:
            continue
        use_preexisting_images = ((not build_enabled) and docker_image.build_status == constants.NOT_BUILT)
        if docker_image.build_status == constants.SUCCESS or use_preexisting_images:
            ecr_url_to_test = docker_image.ecr_url
            if use_latest_additional_tag and len(docker_image.additional_tags) > 0:
                ecr_url_to_test = f"{docker_image.repository}:{docker_image.additional_tags[-1]}"

            # Run sanity tests on the all images built
            DLC_IMAGES["sanity"].append(ecr_url_to_test)
            image_job_type = docker_image.info.get("image_type")
            image_device_type = docker_image.info.get("device_type")
            image_python_version = docker_image.info.get("python_version")
            image_tag = f"{image_job_type}_{image_device_type}_{image_python_version}"
            # when image_run_test_types has key all values can be (all , ecs, eks, ec2, sagemaker)
            if constants.ALL in JobParameters.image_run_test_types.keys():
                run_tests = JobParameters.image_run_test_types.get(constants.ALL)
                run_tests = (
                    constants.ALL_TESTS if constants.ALL in run_tests else run_tests
                )
                for test in run_tests:
                    DLC_IMAGES[test].append(ecr_url_to_test)
            # when key is training or inference values can be  (ecs, eks, ec2, sagemaker)
            if image_job_type in JobParameters.image_run_test_types.keys():
                run_tests = JobParameters.image_run_test_types.get(image_job_type)
                for test in run_tests:
                    DLC_IMAGES[test].append(ecr_url_to_test)
            # when key is image_tag (training-cpu-py3) values can be (ecs, eks, ec2, sagemaker)
            if image_tag in JobParameters.image_run_test_types.keys():
                run_tests = JobParameters.image_run_test_types.get(image_tag)
                run_tests = (
                    constants.ALL_TESTS if constants.ALL in run_tests else run_tests
                )
                for test in run_tests:
                    DLC_IMAGES[test].append(ecr_url_to_test)

    for test_type in DLC_IMAGES.keys():
        test_images = DLC_IMAGES[test_type]
        if test_images:
            DLC_IMAGES[test_type] = list(set(test_images))
    return DLC_IMAGES


def write_to_json_file(file_name, content):
    with open(file_name, "w") as fp:
        json.dump(content, fp)


def set_test_env(images, use_latest_additional_tag=False, images_env="DLC_IMAGES", **kwargs):
    """
    Util function to write a file to be consumed by test env with necessary environment variables

    ENV variables set by os do not persist, as a new shell is instantiated for post_build steps

    :param images: List of image objects
    :param images_env: Name for the images environment variable
    :param env_file: File to write environment variables to
    :param kwargs: other environment variables to set
    """
    test_envs = []

    test_images_dict = fetch_dlc_images_for_test_jobs(images, use_latest_additional_tag=use_latest_additional_tag)

    # dumping the test_images to dict that can be used in src/start_testbuilds.py
    write_to_json_file(constants.TEST_TYPE_IMAGES_PATH, test_images_dict)

    LOGGER.debug(f"Utils Test Type Images: {test_images_dict}")

    if kwargs:
        for key, value in kwargs.items():
            test_envs.append({"name": key, "value": value, "type": "PLAINTEXT"})

    write_to_json_file(constants.TEST_ENV_PATH, test_envs)


def get_codebuild_project_name():
    # Default value for codebuild project name is "local_test" when run outside of CodeBuild
    return os.getenv("CODEBUILD_BUILD_ID", "local_test").split(":")[0]


def get_root_folder_path():
    """
    Extract the root folder path for the repository.

    :return: str
    """
    root_dir_pattern = re.compile(r"^(\S+deep-learning-containers)")
    pwd = os.getcwd()
    codebuild_src_dir_env = os.getenv("CODEBUILD_SRC_DIR")
    root_folder_path = codebuild_src_dir_env if codebuild_src_dir_env else root_dir_pattern.match(pwd).group(1)

    return root_folder_path


def get_safety_ignore_dict(image_uri, framework, python_version, job_type):
    """
    Get a dict of known safety check issue IDs to ignore, if specified in file ../data/ignore_ids_safety_scan.json.

    :param image_uri: str, consists of f"{image_repo}:{image_tag}"
    :param framework: str, framework like tensorflow, mxnet etc.
    :param python_version: str, py2 or py3
    :param job_type: str, type of training job. Can be "training"/"inference"
    :return: dict, key is the ignored vulnerability id and value is the reason to ignore it
    """
    if job_type == "inference":
        job_type = (
            "inference-eia" if "eia" in image_uri else "inference-neuron" if "neuron" in image_uri else "inference"
        )
    
    if "habana" in image_uri:
        framework = f"habana_{framework}"

    ignore_safety_ids = {}
    ignore_data_file = os.path.join(os.sep, get_root_folder_path(), "data", "ignore_ids_safety_scan.json")
    with open(ignore_data_file) as f:
        ignore_safety_ids = json.load(f)

    return ignore_safety_ids.get(framework, {}).get(job_type, {}).get(python_version, {})


def generate_safety_report_for_image(image_uri, image_info, storage_file_path=None):
    """
    Genereate safety scan reports for an image and store it at the location specified 

    :param image_uri: str, consists of f"{image_repo}:{image_tag}"
    :param image_info: dict, should consist of 3 keys - "framework", "python_version" and "image_type".
    :param storage_file_path: str, looks like "storage_location.json"
    :return: list[dict], safety report generated by SafetyReportGenerator
    """
    ctx = Context()
    docker_run_cmd = f"docker run -id --entrypoint='/bin/bash' {image_uri} "
    container_id = ctx.run(f"{docker_run_cmd}", hide=True, warn=True).stdout.strip()
    install_safety_cmd = "pip install safety"
    docker_exec_cmd = f"docker exec -i {container_id}"
    ctx.run(f"{docker_exec_cmd} {install_safety_cmd}", hide=True, warn=True)
    ignore_dict = get_safety_ignore_dict(
        image_uri, image_info["framework"], image_info["python_version"], image_info["image_type"]
    )
    safety_scan_output = SafetyReportGenerator(container_id, ignore_dict=ignore_dict).generate()
    ctx.run(f"docker rm -f {container_id}", hide=True, warn=True)
    if storage_file_path:
        with open(storage_file_path, "w", encoding="utf-8") as f:
            json.dump(safety_scan_output, f, indent=4)
    return safety_scan_output
