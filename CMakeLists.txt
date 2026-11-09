cmake_minimum_required(VERSION 3.22)
file(READ "${CMAKE_CURRENT_LIST_DIR}/launcher/metadata.json" GAIJIN_METADATA_JSON)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
             "${CMAKE_CURRENT_LIST_DIR}/launcher/metadata.json")
string(JSON GAIJIN_VERSION GET "${GAIJIN_METADATA_JSON}" version)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(linux-loader VERSION ${GAIJIN_VERSION})
