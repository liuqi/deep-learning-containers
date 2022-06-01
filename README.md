## VMware Deep Learning Containers

VMware [Deep Learning Containers (DLCs)]
are a set of Docker images for training and serving models in TensorFlow, PyTorch, MXNet and PaddlePaddle. 
Deep Learning Containers provide optimized environments with TensorFlow and MXNet, Nvidia CUDA (for GPU instances), and Bitfusion. 

For the list of available DLC images, see [Available Deep Learning Containers Images](available_images.md). 

## License

This project is licensed under the Apache-2.0 License.

## Table of Contents

 [Getting Started](#getting-started)

 [Building your Image](#building-your-image)

 [Running Tests Locally](#running-tests-locally)

### Getting started

We describe here the setup to build and test the DLCs.

We take an example of building a ***Pytorch GPU python3 training*** container.

* Ensure you have access to an harbor-repo account. [website](https://harbor-repo.vmware.com/)  
* Export an repository with the name “pytorch-inference”.
* Ensure you have [docker](https://docs.docker.com/get-docker/) client set-up on your system.

1. Clone the repo and set the following environment variables: 
    ```shell script
    export REGISTRY=harbor-repo.vmware.com
    export REPOSITORY_NAME=pytorch-inference
    ``` 
2. Login to harbor-repo
    ```shell script
    docker login harbor-repo.vmware.com -u 'Username'
    ``` 
3. Assuming your working directory is the cloned repo, create a virtual environment to use the repo and install requirements
    ```shell script
    git clone https://github.com/AmyHoney/deep-learning-containers.git

    python3 -m venv dlc #create a virtual environment
    source dlc/bin/activate

    cd deep-learning-containers 
    pip install -r src/requirements.txt
    ``` 
4. Perform the initial setup
    ```shell script
    bash src/setup.sh pytorch
    ```
### Building your image

The paths to the dockerfiles follow a specific pattern e.g., pytorch/inference/docker/\<version>/\<python_version>/Dockerfile.<processor>

These paths are specified by the buildspec.yml residing in pytorch/buildspec.yml i.e. \<framework>/buildspec.yml. 
If you want to build the dockerfile for a particular version, or introduce a new version of the framework, re-create the 
folder structure as per above and modify the buildspec.yml file to specify the version of the dockerfile you want to build.

1. To build all the dockerfiles specified in the buildspec.yml locally, use the command
    ```shell script
    python src/main.py --buildspec pytorch/buildspec.yml --framework pytorch
    ``` 
    The above step should take a while to complete the first time you run it since it will have to download all base layers 
    and create intermediate layers for the first time. 
    Subsequent runs should be much faster.
2. If you would instead like to build only a single image
    ```shell script
    python src/main.py --buildspec pytorch/buildspec.yml \
                       --framework pytorch \
                       --image_types inference \
                       --device_types cpu \
                       --py_versions py3
    ```
3. The arguments —image_types, —device_types and —py_versions are all comma separated list who’s possible values are as follows:
    ```shell script
    --image_types <training/inference>
    --device_types <cpu/gpu>
    --py_versions <py2/py3>
    ```

### Upgrading the framework version
1. Suppose, if there is a new framework version for Pytorch (version 1.11.0) then this would need to be changed in the 
buildspec.yml file for Pytorch.
    ```yaml
    # pytorch/buildspec.yml
      1   registry: &REGISTRY <set-$REGISTRY-in-environment>
      3   framework: &FRAMEWORK pytorch
      4   version: &VERSION 1.10.0 *<--- Change this to 1.11.0*
          ................
    ```
2. The dockerfile for this should exist at pytorch/docker/1.11/py3/Dockerfile.cpu. This path is dictated by the 
docker_file key for each repository. 
    ```yaml
    # pytorch/buildspec.yml
     41   images:
     42     BuildPyTorchCPUPTInferencePy3DockerImage:
     43       <<: *TRAINING_REPOSITORY
              ...................
     49       docker_file: !join [ docker/, *VERSION, /, *DOCKER_PYTHON_VERSION, /Dockerfile., *DEVICE_TYPE ]
     
    ```
3. Build the container as described above.

### Running object detection workload by the above container image
1. Obtain object detection model and sample from github
    ```shell script
    git clone https://github.com/AmyHoney/torchserve_od_sample.git
    ```
2. Run with build docker image before and start container using jupyter without password
    ```shell script
    docker run -it -p 80:8888 -v <local_dir>:<container_dir> <Docker_image_id> jupyter notebook --notebook-dir=/ --ip=0.0.0.0 --no-browser --allow-root --port=8888 --NotebookApp.token='' --NotebookApp.password='' --NotebookApp.allow_origin='*' --NotebookApp.base_url=/
    # example
    docker run -it -p 80:8888 -v ~/workspace/torchserve_od_sample:/torchserve_od_sample harbor-repo.vmware.com/zyajing/pytorch-inference:1.11.0-cpu-py38-ubuntu20.04-2022-05-28-08-38-30-multistage-common jupyter notebook --notebook-dir=/ --ip=0.0.0.0 --no-browser --allow-root --port=8888 --NotebookApp.token='' --NotebookApp.password='' --NotebookApp.allow_origin='*' --NotebookApp.base_url=/
    ```
3. Open Jupyter UI http://<vm_ip>:80

4. New a terminal, download the pre-trained fast-rcnn object detection model's state_dict, generate model and start model by torchserve in this new terminal
    ```shell script
    wget -c https://download.pytorch.org/models/fasterrcnn_resnet50_fpn_coco-258fb6c6.pth -P ./models
    # Download FastRCNN model weights 
    cd /torchserve_od_sample
    sh scripts/get_fastrcnn.sh
    # Archive model
    sh scripts/archive_model.sh # the model is stored ./model-store
    #Start TorchServe
    sh scripts/start_torchserve.sh
    ```
5. Run sample inference using REST APIs
    ```shell script
    curl http://127.0.0.1:8080/predictions/fastrcnn -T ./samples/Naxos_Taverna.jpg
    ```
   Or iteratively run the "object_dectection.ipynb" notebook.

