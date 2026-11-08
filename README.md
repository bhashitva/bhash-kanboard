# Bhash Kanboard

Bhash Kanboard is a planned port of Kanboard to Bhash. It will demonstrate a self-hosted project management application with a Hindi and English browser interface, a native Bhash server, and live collaboration. [Bhash](https://github.com/bhashitva/bhash) · [Upstream Kanboard](https://github.com/kanboard/kanboard)

## First milestone

1. Import one Kanboard project with its columns, swimlanes, cards, and card order.
2. Sign in as two users and move a card. Both browsers should show the move after it commits.
3. Restart the server and verify the move persisted. Test conflicting edits and verify that users outside the project receive neither board data nor live updates.

The first release will pin a published Bhash compiler. SQLite will serve local development and demos, with PostgreSQL for shared deployments. The Bhash live task board is the reference for authentication, database transactions, and WebSocket updates. [Live task board](https://github.com/bhashitva/bhash/tree/main/examples/live-task-board)

## Upstream attribution

Kanboard is developed by Frédéric Guillot and contributors and is distributed under the MIT license. [Upstream license](https://github.com/kanboard/kanboard/blob/main/LICENSE)
