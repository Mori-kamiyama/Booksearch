build-HomePublisherFunction:
	test -f render/entry-home.js && test -f client-index.html
	cp *.mjs "$(ARTIFACTS_DIR)/"
	cp package.json "$(ARTIFACTS_DIR)/"
	cp package-lock.json "$(ARTIFACTS_DIR)/"
	cd "$(ARTIFACTS_DIR)" && npm ci --omit=dev --os=linux --cpu=arm64 --libc=glibc
	node install-linux-image-deps.mjs "$(ARTIFACTS_DIR)"
	test -f "$(ARTIFACTS_DIR)/node_modules/@img/sharp-linux-arm64/package.json"
	test -f "$(ARTIFACTS_DIR)/node_modules/@img/sharp-libvips-linux-arm64/package.json"
	cp client-index.html "$(ARTIFACTS_DIR)/"
	cp -R render "$(ARTIFACTS_DIR)/render"
