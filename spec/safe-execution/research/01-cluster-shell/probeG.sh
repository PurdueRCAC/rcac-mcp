echo "--- scontrol -- show node -probe"; scontrol -- show node -probe 2>&1 | head -2
echo "--- squeue --me -p -probe"; squeue --me -p -probe 2>&1 | head -2
echo "--- sinfo -p -probe"; sinfo -p -probe 2>&1 | head -2
echo "--- sacct -X -n -S now -A -probe"; sacct -X -n -S now -A -probe 2>&1 | head -2; echo rc=$?
echo "--- sbatch --version"; sbatch --version
echo "--- ls --time-style=long-isoa (today's show_hidden flags)"; ls -l --time-style=long-isoa / >/dev/null 2>&1; echo rc=$?; ls -l --time-style=long-isoa / 2>&1 | head -2
echo "--- site tools: non-login vs login bash"; for s in myquota findscratch showpartitions slist sfeatures average_wait jobinfo jobcmd jobenv jobscript; do printf "%-15s -c:%s  -lc:%s\n" $s "$(/bin/bash -c "type -P $s" || echo MISSING)" "$(/bin/bash -lc "type -P $s" || echo MISSING)"; done
echo "--- readlink"; readlink --version | head -1
