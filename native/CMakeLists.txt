cmake_minimum_required(VERSION 3.20)
project(AtracsysBridge LANGUAGES CXX)

if(NOT WIN32 OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "The bridge requires Windows x64.")
endif()
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

set(ATRACSYS_SDK_ROOT "C:/Program Files/Atracsys/fusionTrack SDK x64"
    CACHE PATH "Locally licensed Atracsys SDK (not distributed by this project)")
set(Atracsys_DIR "${ATRACSYS_SDK_ROOT}/cmake/Atracsys")
find_package(Atracsys REQUIRED COMPONENTS SDK)

add_executable(atracsys_bridge bridge_main.cpp shared_protocol.hpp frame_policy.hpp measurement_protocol.hpp measurement_memory.hpp parent_watch.hpp)
target_link_libraries(atracsys_bridge PRIVATE Atracsys::SDK)
target_compile_definitions(atracsys_bridge PRIVATE UNICODE _UNICODE)

# This test only exercises memory layouts and synthetic records. It never loads
# the SDK or starts the bridge, enumerates devices, or opens a camera.
include(CTest)
if(BUILD_TESTING)
    add_executable(bridge_protocol_test tests/protocol_test.cpp)
    target_include_directories(bridge_protocol_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME bridge_protocol COMMAND bridge_protocol_test)
    add_executable(bridge_measurement_test tests/measurement_test.cpp)
    target_include_directories(bridge_measurement_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME bridge_measurement COMMAND bridge_measurement_test)
    add_executable(bridge_parent_watch_test tests/parent_watch_test.cpp)
    target_include_directories(bridge_parent_watch_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME bridge_parent_watch COMMAND bridge_parent_watch_test)
    set_tests_properties(bridge_parent_watch PROPERTIES TIMEOUT 12)
endif()

# Deliberately no install/deploy target: existing runtimes remain untouched.
