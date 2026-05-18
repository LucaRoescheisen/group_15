@echo off
set CHERE_INVOKING=true
set LANG=en_US.UTF-8
set ISABELLE_HOME=C:\Program Files\Isabelle2025-2
"C:\Program Files\Isabelle2025-2\contrib\cygwin\bin\bash.exe" --login -c "/cygdrive/c/Program\ Files/Isabelle2025-2/bin/isabelle %*"
