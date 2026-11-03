"""Remove duplicate schedule rows without choosing between different programmes."""


def _content(node, root=False):
    return (node.tag, tuple(sorted((k, v) for k, v in node.attrib.items()
                                  if not (root and k == 'stop'))),
            (node.text or '').strip(), tuple(_content(child) for child in node))


def same_start_duplicates(programmes, parse_time):
    """Keep the shortest observed stop for otherwise identical same-start rows.

    Every surviving interval was supplied by the provider. Different titles,
    descriptions, episode metadata, languages or start times remain separate.
    No start/stop is invented and input elements are never edited.
    """
    winners = {}
    dropped = []
    for node in programmes:
        key = _content(node, root=True)
        previous = winners.get(key)
        if previous is None:
            winners[key] = node
        elif parse_time(node.get('stop')) < parse_time(previous.get('stop')):
            dropped.append(previous)
            winners[key] = node
        else:
            dropped.append(node)
    rejected = {id(node) for node in dropped}
    return [node for node in programmes if id(node) not in rejected], dropped
