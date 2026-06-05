# SAM Makefile builder for Go Lambda. Invoked by `sam build` with target build-<LogicalId>.
# Uses BuildMethod: makefile in template.yaml.

build-ApiFunction:
	GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -tags lambda.norpc -o $(ARTIFACTS_DIR)/bootstrap .
	if [ -f ./library.db ]; then cp ./library.db $(ARTIFACTS_DIR)/library.db; else cp ../../../outputs/library/library.db $(ARTIFACTS_DIR)/library.db; fi
