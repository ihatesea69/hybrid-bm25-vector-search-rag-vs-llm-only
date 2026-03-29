FROM pgvector/pgvector:pg17

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      git \
      postgresql-server-dev-17 && \
    git clone --depth=1 https://github.com/timescale/pg_textsearch /tmp/pg_textsearch && \
    make -C /tmp/pg_textsearch && \
    make -C /tmp/pg_textsearch install && \
    rm -rf /tmp/pg_textsearch && \
    apt-get purge -y build-essential git postgresql-server-dev-17 && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

RUN printf "shared_preload_libraries = 'pg_textsearch'\n" >> /usr/share/postgresql/postgresql.conf.sample
