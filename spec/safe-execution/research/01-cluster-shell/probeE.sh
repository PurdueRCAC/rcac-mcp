echo "--- under bash -l: where the job* tools come from"; /bin/bash -l -c 'type -a jobcmd jobenv jobscript jobinfo 2>&1 | cut -c1-100'
echo "--- under zsh -c (today)"; /bin/zsh -c 'whence -a jobcmd jobenv jobscript jobinfo 2>&1'
echo "--- average_wait option loop"; sed -n 170,195p /usr/local/bin/average_wait
