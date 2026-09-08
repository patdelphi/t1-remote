cmake_minimum_required(VERSION 3.20)

# 程序说明：使用 Windows SDK/MSVC 构建轻量用户态桥接 DLL。
project(t1bridge C)

add_library(t1bridge SHARED t1bridge.c)
target_compile_definitions(t1bridge PRIVATE WIN32_LEAN_AND_MEAN UNICODE _UNICODE)
target_include_directories(t1bridge PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
if(MSVC)
    target_compile_options(t1bridge PRIVATE /utf-8)
endif()
set_target_properties(t1bridge PROPERTIES PREFIX "")
