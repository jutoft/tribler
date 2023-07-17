# libtorrent-1.2.9 does not support 3.11
FROM python:3.10

RUN apt-get update \
    && apt-get install -y libsodium23 git \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -ms /bin/bash user
USER user
WORKDIR /home/user

# Then, install pip dependencies so that it can be cached and does not
# need to be built every time the source code changes.
# This reduces the docker build time.
RUN mkdir requirements
COPY ./requirements-core.txt requirements/core-requirements.txt
RUN pip3 install -r requirements/core-requirements.txt

# Copy the source code and set the working directory
COPY ./ tribler
WORKDIR /home/user/tribler

# Only run the core process with --core switch
CMD ["python3", "/home/user/tribler/src/run_tribler.py", "--core"]
