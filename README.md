# भाष कार्यपट · Bhash Kanboard

Kanboard की एक परियोजना को भाष में चलाकर देखें। आयात में परियोजना की पंक्तियाँ
(swimlanes), स्तंभ और कार्ड अपने क्रम में आते हैं। दो अलग ब्राउज़र सत्र उसी
कार्यपट पर काम कर सकते हैं। कार्ड खींचकर छोड़ें, या उसके **स्थान बदलें** बटन से
गंतव्य और क्रम चुनें। दूसरे सत्र को बदलाव WebSocket से मिलता है।

The shared board supports importing, creating, editing, moving, archiving, and restoring cards. The Bhash server handles
validation, authentication, project membership, storage and live events. The
browser handles rendering and input. Import follows the
[Kanboard getBoard API](https://docs.kanboard.org/v1/api/board_procedures/).

## अपने कंप्यूटर पर चलाएँ / Run locally

Use Java 17 or newer and the compiler JAR from
[Bhash VS Code 0.19.0](https://github.com/bhashitva/bhash/releases/tag/vscode-0.19.0).
The commands below use Bash, for example on Linux or WSL.

```sh
mkdir -p out
curl -fL https://github.com/bhashitva/bhash/releases/download/vscode-0.19.0/bhash-vscode-0.19.0.vsix -o out/bhash.vsix
echo 'd22c59742947386214820ce32536b2be19c1dde2d1232537cf6fa3dc6589a975  out/bhash.vsix' | sha256sum -c
unzip -p out/bhash.vsix extension/server/bhash-compiler.jar > out/compiler.jar
java -jar out/compiler.jar compile bhash.project --target jvm -o out/board.jar

read -rsp 'Alice password: ' BHASH_BOARD_ALICE_PASSWORD; echo
read -rsp 'Bob password: ' BHASH_BOARD_BOB_PASSWORD; echo
read -rsp 'Charlie password: ' BHASH_BOARD_CHARLIE_PASSWORD; echo
export BHASH_BOARD_ALICE_PASSWORD BHASH_BOARD_BOB_PASSWORD BHASH_BOARD_CHARLIE_PASSWORD

java -Dfile.encoding=UTF-8 -jar out/board.jar --local=true --port=8080 \
  --db=out/demo.db --import=demo/get-board.json
```

हर खाते का अलग कूटशब्द रखें और कम से कम १२ अक्षर दें। एक सामान्य विंडो में
`alice` और निजी विंडो में `bob` से [स्थानीय कार्यपट](http://127.0.0.1:8080)
खोलें। कार्ड को दूसरी पंक्ति के स्तंभ में ले जाएँ। दोनों विंडो में उसका नया
स्थान दिखेगा। **कार्ड जोड़ें** से नया काम बनाएँ और **संपादित करें** से उसका
शीर्षक या विवरण बदलें। **संग्रहित करें** कार्ड को सक्रिय कार्यपट से हटाता है।
नीचे **संग्रह** खोलकर **वापस लाएँ** दबाने से कार्ड अपने पुराने स्तंभ और
उपलब्ध पुराने स्थान पर लौटता है। `charlie` का अपना खाली कार्यपट है। सर्वर को उसी `--db` के साथ
दोबारा चलाएँ और इस बार `--import` हटाएँ। फिर प्रवेश करने पर पिछला क्रम दिखेगा।

For native, build with `--target native -o out/board` and run `./out/board`
with the same arguments. See [Bhash's native toolchain setup](https://github.com/bhashitva/bhash/blob/main/docs/managed-native-toolchain.md).
For .NET, use `--target clr -o out/clr` and run `dotnet out/clr/BhashProgram.dll`.

`--local=true` is for loopback HTTP. For deployment behind a TLS reverse proxy,
omit it and set `--origin=https://your-board.example`. Forward HTTP requests and
WebSocket upgrades to the loopback Bhash server. The three environment-configured
accounts and fixed project memberships are the demo's access model.

## अपनी Kanboard परियोजना लाएँ / Import your own board

Save the JSON-RPC response for one project. For example, request project 42
using an account allowed to read it. Curl prompts for the API token as the HTTP
Basic password. Keep your export outside Git if it contains private board data.
[Kanboard API authentication](https://docs.kanboard.org/v1/api/authentication/)

```sh
curl --fail-with-body --user YOUR_KANBOARD_USERNAME \
  --header 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"getBoard","id":1,"params":{"project_id":42}}' \
  https://YOUR_KANBOARD_HOST/jsonrpc.php --output out/kanboard-export.json
```

Use `--db=out/imported.db --import=out/kanboard-export.json` on first startup.
The app accepts numeric and string IDs, including swimlane ID 0. It preserves
numeric positions and uses source order when positions are absent. It imports
active cards returned by `getBoard` and retains their titles and descriptions.
The demo accepts up to 16 swimlanes, 16 columns per swimlane and 250 cards total,
with a 2 MiB export limit and a 384 KiB normalized board limit.
The 250-card limit includes archived cards. Titles allow up to 400 UTF-8 bytes
and descriptions up to 4,096 UTF-8 bytes. Invalid imports
leave the board untouched. Use a fresh database to import a different project.

## बदलाव कैसे सहेजा जाता है / How a move is saved

1. `src/import.bhash` उत्तर की जाँच करके कार्ड और स्तंभ क्रम में रखता है।
2. `src/storage.bhash` SQLite में कार्यपट और उसका संस्करण एक साथ रखता है।
   चाल में नया क्रम तभी सहेजा जाता है जब डेटाबेस में पुराना संस्करण अभी भी
   मेल खाता हो। टकराव पर HTTP 409 मिलता है और पृष्ठ नया क्रम दिखाता है।
3. `src/security.bhash` सत्र, परियोजना और WebSocket प्रवेश की जाँच करता है।
   SQLite में सफल बदलाव के बाद सर्वर नया संस्करण भेजता है। दूसरा सत्र अपना
   अधिकृत कार्यपट फिर पढ़ता है। WebSocket फिर जुड़ने पर भी पृष्ठ नया क्रम पढ़ता है।

SQLite holds one versioned JSON snapshot per board for this milestone. The
active and archived cards share the same atomic update and board revision. It also sets a practical scale limit and keeps the
demo small. Attachments, comments, users, historical cards, and live two-way
Kanboard synchronization are outside the import scope.

## Verification

```sh
python -m pip install playwright==1.58.0 websocket-client==1.9.0
python -m playwright install chromium
python tests/smoke.py --browser -- java -Dfile.encoding=UTF-8 -jar out/board.jar
```

The smoke test opens two browser contexts and checks that both receive the
committed WebSocket update. It also checks card order, CSRF, project isolation,
stale and concurrent moves, same-column reorder, creation, edits and stale
drafts, archiving, restoration, upgrade of existing databases, and persistence
after restart.
CI exercises the JVM, .NET and Linux native builds. The same test without
`--browser` uses two direct WebSocket clients.

The server and session scaffolding are adapted from the Apache-2.0
[Bhash live task board](https://github.com/bhashitva/bhash/tree/main/examples/live-task-board).
Kanboard is the upstream reference. [Kanboard license](https://github.com/kanboard/kanboard/blob/main/LICENSE)
