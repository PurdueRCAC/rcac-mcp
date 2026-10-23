echo "--- scontrol show node -x (getopt permutation?)"; scontrol show node -x 2>&1 | head -3
echo "--- scontrol show node -- -x"; scontrol show node -- -x 2>&1 | head -3
echo "--- scontrol show node=-x"; scontrol show node=-x 2>&1 | head -3
echo "--- squeue --user=-x"; squeue --user=-x 2>&1 | head -3
echo "--- squeue -u -x"; squeue -u -x 2>&1 | head -3
echo "--- squeue --me -- extra"; squeue --me -- extra 2>&1 | head -3
echo "--- sbatch --usage"; sbatch --usage 2>&1 | head -4
echo "--- slurm version"; sinfo --version
echo "--- site tools"; for t in myquota jobinfo jobcmd jobenv jobscript average_wait slist sfeatures showpartitions findscratch; do p=$(command -v $t); printf "%s -> %s : %s\n" "$t" "${p:-MISSING}" "$( [ -n "$p" ] && file -b "$p" | cut -c1-60)"; done
echo "--- average_wait arg parsing"; p=$(command -v average_wait) && grep -n -E "getopt|argparse|add_argument|case|\\\$1|-p|-A" "$p" | head -25
echo "--- jobinfo arg parsing"; p=$(command -v jobinfo) && grep -n -E "getopt|argparse|add_argument|\\\$1|sys.argv" "$p" | head -10
echo "--- coreutils"; cat --version | head -1; ls --version | head -1; base64 --version | head -1
