#!/bin/bash
mkdir -p /tmp/long_task_test
rm -f /tmp/long_task_test/*

for i in {1..100}
do
    echo "Content for file $i" > "/tmp/long_task_test/file_$i.txt"
    sleep 1
    if [ $((i % 10)) -eq 0 ]; then
        echo "Progress: $i/100 files created."
    fi
done

ls /tmp/long_task_test/file_*.txt > /tmp/long_task_test/summary.txt
echo "Task completed. Summary created."
