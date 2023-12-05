bindkey -v
stty -ixon
bindkey '^R' history-incremental-search-backward
bindkey '^S' history-incremental-search-forward
zstyle ':completion:*' menu select
LISTMAX=10000
