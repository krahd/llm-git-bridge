# Hot-path acceptance

This file records the acceptance probe after branch snapshot publication was removed from the default synchronous transaction path.

The probe intentionally runs no repository commands so its timing isolates bridge, Git, push, and mailbox overhead.
