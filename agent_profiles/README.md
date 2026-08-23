# Custom Agent Profiles

Place trusted declarative `*.json` Profiles in this directory. They may compose
registered Capability Packs, select a knowledge scope and configure UI features;
they cannot import Python modules or execute configuration code.

Copy [`support.example.json`](support.example.json) to `support.json` to make the
example discoverable. Profile ids must be unique across built-in and local
definitions. Existing conversations remain bound to their original id, version
and manifest hash, so increment `version` whenever behavior changes.

The current filesystem provider maps every managed document to the `default`
library. Other `library_ids` yield no search results and direct reads fail closed;
Knowledge V2.1 can add more persisted Library/Source identities without changing
the Profile contract.
