import os
import subprocess
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.event_handlers import OnProcessExit
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, RegisterEventHandler
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, Command, FindExecutable
from launch_ros.substitutions import FindPackageShare
from ament_index_python.packages import get_package_share_directory
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.actions import AppendEnvironmentVariable, ExecuteProcess, TimerAction, OpaqueFunction
import random
import math
from typing import List, Dict

package_name = 'ros2_ur5_interface'


def generate_random_rotation():
    """Generate random rotation only around Z axis to keep blocks upright"""
    # Only rotate around Z-axis (yaw) to keep blocks on their base
    return (0, 0, random.uniform(0, 2 * math.pi))

def check_collision(new_pos: Dict, existing_positions: List[Dict], min_distance: float = 0.15) -> bool:
    """Check if new position collides with existing blocks"""
    for pos in existing_positions:
        dx = float(new_pos['x']) - float(pos['x'])
        dy = float(new_pos['y']) - float(pos['y'])
        distance = math.sqrt(dx*dx + dy*dy)
        if distance < min_distance:
            return True
    return False

def generate_balanced_colors(num_blocks: int) -> List[str]:
    """Generate a balanced list of colors"""
    colors = [
        "1.0 1.0 0.0 1",  # Yellow
        "0.0 1.0 0.0 1",  # Green
        "1.0 0.0 0.0 1",  # Red
        "1.0 0.5 0.0 1"   # Orange
    ]
    
    # Calculate base number of each color
    base_count = num_blocks // len(colors)
    remainder = num_blocks % len(colors)
    
    # Create balanced color list
    result = colors * base_count
    if remainder:
        result.extend(random.sample(colors, remainder))
    
    random.shuffle(result)
    return result

def spawn_block(context, *args, **kwargs):
    instances_cmds = []
    

    block_types = ["X1-Y1-Z2", "X1-Y2-Z2", "X1-Y4-Z2", "X1-Y2-Z1", "X1-Y3-Z2-FILLET", 
                   "X1-Y2-Z2-CHAMFER", "X1-Y3-Z2", "X1-Y2-Z2-TWINFILLET", "X1-Y4-Z1"]
    
    # Randomly choose 4 or 5 blocks
    num_blocks = random.randint(4, 5)
    
    # Ensure unique block types
    selected_types = random.sample(block_types, num_blocks)
    colors = generate_balanced_colors(num_blocks)
    
    base_x, base_y = 0.1, 0.45
    existing_positions = []
    blocks_config = []

    for i in range(num_blocks):
        while True:
            offset_x = random.uniform(-0.1, 0.1)
            offset_y = random.uniform(-0.25, 0.25)
            rot_x, rot_y, rot_z = generate_random_rotation()
            
            new_pos = {
                "number": str(i + 1),
                "type": selected_types[i],
                "x": str(base_x + offset_x),
                "y": str(base_y + offset_y),
                "z": "0.88",  # Height from table surface
                "R": str(rot_x),
                "P": str(rot_y),
                "Y": str(rot_z),
                "color": colors[i]
            }
            
            if not check_collision(new_pos, existing_positions):
                existing_positions.append(new_pos)
                blocks_config.append(new_pos)
                break


    for block in blocks_config:
        # Paths for current block
        xacro_file = os.path.join(get_package_share_directory(package_name), 'models', 'block.urdf.xacro')
        urdf_file = os.path.join(get_package_share_directory(package_name), 'models', f'block{block["number"]}.urdf')
        sdf_file = os.path.join(get_package_share_directory(package_name), 'models', f'block{block["number"]}.sdf')

        # Generate URDF from Xacro
        try:
            xacro_command = [
                FindExecutable(name="xacro").perform(context),
                xacro_file,
                f"block_name:={block['number']}",
                f"block_type:={block['type']}",
                f"block_color:={block['color']}"
            ]
            urdf_output = subprocess.check_output(xacro_command, text=True)
            with open(urdf_file, 'w') as urdf_fp:
                urdf_fp.write(urdf_output)

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Error generating URDF: {e}")

        # Convert URDF to SDF
        try:
            sdf_command = [
                FindExecutable(name="gz").perform(context),
                "sdf",
                "-p",
                urdf_file,
            ]
            sdf_output = subprocess.check_output(sdf_command, text=True)
            sdf_lines = sdf_output.splitlines()
            sensor_block = f"""
            <plugin
                filename="ignition-gazebo-pose-publisher-system"
                name="ignition::gazebo::systems::PosePublisher">
                <publish_model_pose>true</publish_model_pose>
                <publish_nested_model_pose>true</publish_nested_model_pose>
                <use_pose_vector_msg>true</use_pose_vector_msg>
                <update_frequency>100.0</update_frequency>
            </plugin>
            """
            insert_index = next(
                (i for i, line in enumerate(sdf_lines) if "</model>" in line), len(sdf_lines) - 1
            )
            sdf_lines.insert(insert_index, sensor_block)
            modified_sdf_output = "\n".join(sdf_lines)
            with open(sdf_file, 'w') as sdf_fp:
                sdf_fp.write(modified_sdf_output)

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Error converting URDF to SDF: {e}")

        # Block robot state publisher node
        block_robot_state_publisher_node = Node(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            output='screen',
            namespace=f'block{block["number"]}',
            name='robot_state_publisher',
            parameters=[{'robot_description': urdf_output}]
        )
        instances_cmds.append(block_robot_state_publisher_node)

        spawn_block = Node(
            package='ros_gz_sim',
            executable='create',
            arguments=[
                '-name', f"block{block['number']}",
                '-file', sdf_file,
                '-x', block['x'],
                '-y', block['y'],
                '-z', block['z'],
                '-R', block['R'],
                '-P', block['P'],
                '-Y', block['Y']
            ],
            output='screen',
        )
        instances_cmds.append(spawn_block)

    return instances_cmds


def generate_launch_description():
    declared_arguments = []

    declared_arguments.append(
        DeclareLaunchArgument(
            "ur_type",
            description="Type/series of used UR robot.",
            choices=["ur3", "ur3e", "ur5", "ur5e", "ur10", "ur10e", "ur16e", "ur20", "ur30"],
            default_value="ur5e",
        )
    )

    ur_type = LaunchConfiguration("ur_type")
    world_file = os.path.join(get_package_share_directory(package_name),'worlds','empty.world')
    rviz_config_file = os.path.join(get_package_share_directory(package_name), 'rviz', 'ur5.rviz')
    desk_urdf = Command([PathJoinSubstitution([FindExecutable(name='xacro')])," ",PathJoinSubstitution([FindPackageShare(package_name), "models", "desk.urdf.xacro"])])
    camera_sdf = os.path.join(get_package_share_directory(package_name), 'models', 'camera.sdf')

    robot_description_content = Command(
        [
            PathJoinSubstitution([FindExecutable(name="xacro")]),
            " ",
            PathJoinSubstitution([FindPackageShare(package_name), "models", "ur_gz.urdf.xacro"]),
            " ",
            "safety_limits:=", "true",
            " ",
            "safety_pos_margin:=", "0.15",
            " ",
            "safety_k_position:=", "20",
            " ",
            "name:=", "ur",
            " ",
            "ur_type:=", ur_type,
            " ",
            "tf_prefix:=", "",
            " ",
            "simulation_controllers:=",
            PathJoinSubstitution([FindPackageShare(package_name), "config", "ur_controllers.yaml"]),
        ]
    )

    set_env_vars = AppendEnvironmentVariable(
        'GZ_SIM_RESOURCE_PATH',
        os.path.join(get_package_share_directory(package_name), 'models') +
        ':' +
        os.path.dirname(get_package_share_directory('ur_description'))
    )

    # Fixed transform broadcaster
    fixed_tf_broadcast = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        output='screen',
        arguments=['0', '0', '0', '0', '0', '0', 'desk', 'default']
    )

    # Fixed transform broadcaster for the camera
    fixed_camera_tf_broadcast = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        output='screen',
        arguments=['-0.5', '0.5', '1.2', '-0.06', '0.4', '0.0', 'desk', 'camera_rgb_frame']
    )

    # UR robot state publisher node
    ur_robot_state_publisher_node = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        output='screen',
        name='robot_state_publisher',
        parameters=[{'robot_description': robot_description_content}]
    )

    # Desk robot state publisher node
    desk_state_publisher_node = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        output='screen',
        namespace='desk',
        name='robot_state_publisher',
        parameters=[{'robot_description': desk_urdf}]
    )

    joint_state_broadcaster_spawner = Node(
        package="controller_manager",
        executable="spawner",
        arguments=["joint_state_broadcaster", "--controller-manager", "/controller_manager"],
    )

    joint_controller_spawner = Node(
        package="controller_manager",
        executable="spawner",
        arguments=["scaled_joint_trajectory_controller", "-c", "/controller_manager"],
    )

    gripper_controller_spawner = Node(
        package="controller_manager",
        executable="spawner",
        arguments=["gripper_controller", "-c", "/controller_manager"],
    )

    gazebo_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([FindPackageShare('ros_gz_sim'), '/launch/gz_sim.launch.py']),
        launch_arguments={'gz_args': ['-r -s ', world_file ], 'on_exit_shutdown': 'true'}.items()
        #                              -r -s -v4
    )
    
    spawn_camera = Node(
        package='ros_gz_sim',
        executable='create',
        arguments=[
            '-name', 'camera',
            '-file', camera_sdf,
            '-x', '-0.5',
            '-y', '0.5',
            '-z', '1.2',
            '-R', '0.0',
            '-P', '0.4',
            '-Y', '-0.06',
        ],
        output='screen',
    )

    spawn_ur5 = Node(
        package='ros_gz_sim',
        executable='create',
        arguments=[
            '-name', ur_type,
            '-string', robot_description_content,
        ],
        output='screen',
    )

    spawn_desk = Node(
        package='ros_gz_sim',
        executable='create',
        arguments=[
            '-name', "desk",
            '-string', desk_urdf,
        ],
        output='screen',
    )

    activate_gripper = Node(
        package='ros2_ur5_interface',
        executable='gripper_service',
        output='screen',
    )

    bridge_params = os.path.join(
        get_package_share_directory(package_name),
        'params',
        'ur5_bridge.yaml'
    )

    gazebo_ros_bridge = Node(
        package='ros_gz_bridge',
        executable='parameter_bridge',
        arguments=[
            '--ros-args',
            '-p',
            f'config_file:={bridge_params}',
        ],
        output='screen',
    )

    gazebo_ros_image_bridge = Node(
        package='ros_gz_image',
        executable='image_bridge',
        arguments=['/camera/image_raw/image'],
        output='screen',
    )

    # RViz2 node
    rviz2 = RegisterEventHandler(
        event_handler=OnProcessExit(
            target_action=joint_state_broadcaster_spawner,
            on_exit=[
                Node(
                    package='rviz2',
                    executable='rviz2',
                    name='rviz2',
                    arguments=['-d', rviz_config_file],
                    output='screen'
                ),
            ],
        ),
    )

    # Return the LaunchDescription
    return LaunchDescription([
        *declared_arguments,
        set_env_vars,
        fixed_tf_broadcast,
        fixed_camera_tf_broadcast,
        desk_state_publisher_node,
        ur_robot_state_publisher_node,
        OpaqueFunction(function=spawn_block),
        joint_state_broadcaster_spawner,
        joint_controller_spawner,
        gripper_controller_spawner,
        gazebo_launch,
        spawn_camera,
        spawn_ur5,
        spawn_desk,
        activate_gripper,
        gazebo_ros_bridge,
        gazebo_ros_image_bridge,
        rviz2,
    ])
