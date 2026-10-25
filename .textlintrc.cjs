const path = require("node:path");

// cloneしたrepoでは、uv syncのeditable installからcheckoutのPythonソースを使う。
const python = path.join(__dirname, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const assets = path.join(__dirname, "packages/textlint-rule-native-japanese/assets");

module.exports = {
    rules: {
        "native-japanese": {
            command: [python, "-E", "-X", "utf8", "-m", "native_japanese", "serve",
                "--reference", path.join(assets, "reference.sqlite3"), "--assets-dir", assets]
        }
    }
};
